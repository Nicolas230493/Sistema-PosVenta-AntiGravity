"""Lógica de compras: Costo Promedio Ponderado y cuenta corriente con proveedores (SPEC-SUP-07)."""
from datetime import timedelta
from decimal import Decimal, ROUND_HALF_UP

from django.db import transaction
from django.utils import timezone

from finance.models import CashExpense, CashSession
from finance.services import refresh_session_totals
from products.models import InventoryMovement, PriceLog, Product

from .models import Supplier, SupplierAccountMovement

MONEY = Decimal('0.01')


class SupplierError(Exception):
    """Error de validación en compras/pagos a proveedores (mensaje apto para el usuario)."""


def weighted_average_cost(current_stock, current_cost, qty_received, purchase_cost):
    """
    SPEC-SUP-07 §7.1:
    CPP = (max(stock, 0) * costo_actual + cantidad * costo_compra) / (max(stock, 0) + cantidad)
    """
    stock = max(current_stock, Decimal('0'))
    total_qty = stock + qty_received
    if total_qty <= 0:
        return purchase_cost.quantize(MONEY, rounding=ROUND_HALF_UP)
    cpp = (stock * current_cost + qty_received * purchase_cost) / total_qty
    return cpp.quantize(MONEY, rounding=ROUND_HALF_UP)


def receive_stock(*, product, quantity, unit_cost, reference, reason, user):
    """
    Ingresa mercadería recalculando el costo por CPP, registra PriceLog (si cambia el costo)
    e InventoryMovement PURCHASE_IN. Debe llamarse dentro de transaction.atomic().
    Si `unit_cost` es None se ingresa al costo vigente (el CPP no cambia).
    """
    product = Product.objects.select_for_update().get(pk=product.pk)
    old_cost = product.cost_price
    if unit_cost is not None:
        new_cost = weighted_average_cost(product.stock, old_cost, quantity, unit_cost)
    else:
        new_cost = old_cost

    product.stock += quantity
    product.cost_price = new_cost
    product.save()

    if new_cost != old_cost:
        PriceLog.objects.create(
            product=product,
            old_price=old_cost,
            new_price=new_cost,
            price_type='COST',
            reason=f"CPP por {reference}",
            user=user,
        )
    InventoryMovement.objects.create(
        product=product,
        quantity=quantity,
        cost_price=unit_cost if unit_cost is not None else old_cost,
        movement_type='PURCHASE_IN',
        reference=reference,
        reason=reason,
        user=user,
    )
    return product


def _register_movement(supplier, movement_type, amount, user, **fields):
    supplier = Supplier.objects.select_for_update().get(pk=supplier.pk)
    if movement_type == SupplierAccountMovement.DEBIT:
        supplier.balance += amount
    else:
        supplier.balance -= amount
    supplier.save(update_fields=['balance'])
    return SupplierAccountMovement.objects.create(
        supplier=supplier,
        movement_type=movement_type,
        amount=amount,
        balance_after=supplier.balance,
        user=user,
        **fields,
    )


def receive_purchase_order(*, order, user, due_date=None):
    """
    Recepción de una Orden de Compra: CPP por ítem, stock, PriceLog, PURCHASE_IN y deuda
    (DEBIT) en la cuenta corriente del proveedor. Todo en una única transacción.
    """
    from products.models import PurchaseOrder

    with transaction.atomic():
        order = PurchaseOrder.objects.select_for_update().select_related('supplier').get(pk=order.pk)
        if order.status in ('RECEIVED', 'PAID'):
            raise SupplierError(f"La Orden #{order.id} ya fue recibida.")
        if order.status == 'CANCELLED':
            raise SupplierError(f"La Orden #{order.id} está cancelada.")

        total = Decimal('0.00')
        for detail in order.details.select_related('product'):
            qty = Decimal(detail.quantity)
            receive_stock(
                product=detail.product,
                quantity=qty,
                unit_cost=detail.estimated_cost,
                reference=f"Orden Compra #{order.id}",
                reason=f"Recepción OC #{order.id} - Proveedor: {order.supplier.name}",
                user=user,
            )
            total += (qty * detail.estimated_cost).quantize(MONEY)

        order.total_amount = total
        order.status = 'RECEIVED'
        order.save(update_fields=['total_amount', 'status'])

        if total > 0:
            _register_movement(
                order.supplier, SupplierAccountMovement.DEBIT, total, user,
                purchase_order=order,
                due_date=due_date or (timezone.localdate() + timedelta(days=30)),
                reference=f"OC #{order.id}",
            )
    return order


def register_supplier_payment(*, supplier, amount, source, user, purchase_order=None, notes=''):
    """
    Pago a proveedor (CREDIT). Desde caja genera un egreso en el turno abierto;
    desde banco no impacta el efectivo del turno.
    """
    if amount is None or amount <= 0:
        raise SupplierError("El monto del pago debe ser mayor a cero.")
    if source not in (SupplierAccountMovement.SOURCE_CASH, SupplierAccountMovement.SOURCE_BANK):
        raise SupplierError("Origen de pago inválido.")

    with transaction.atomic():
        expense = None
        label = f"Pago OC #{purchase_order.id} - {supplier.name}" if purchase_order else f"Pago a proveedor - {supplier.name}"
        if source == SupplierAccountMovement.SOURCE_CASH:
            session = CashSession.objects.select_for_update().filter(is_open=True).order_by('-id').first()
            if not session:
                raise SupplierError("Debes tener una caja abierta para pagar desde caja.")
            expense = CashExpense.objects.create(
                session=session,
                amount=amount,
                description=label,
                kind=CashExpense.KIND_SUPPLIER_PAYMENT,
            )
            refresh_session_totals(session)

        movement = _register_movement(
            supplier, SupplierAccountMovement.CREDIT, amount, user,
            purchase_order=purchase_order,
            payment_source=source,
            cash_expense=expense,
            reference=label,
            notes=notes or '',
        )
    return movement
