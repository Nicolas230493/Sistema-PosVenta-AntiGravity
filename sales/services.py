"""Lógica de negocio de ventas compartida por el POS online y la sincronización offline."""
import json
import uuid
from datetime import timedelta
from decimal import Decimal, InvalidOperation

from django.db import IntegrityError, transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from core.models import ActivityLog
from customers.models import CurrentAccount, Customer
from finance.models import CashSession, PaymentMethod
from products.models import InventoryMovement, Product, ProductPrice

from .models import Promotion, Sale, SaleDetail, SalePayment

QTY_PLACES = Decimal('0.001')
MONEY = Decimal('0.01')
DEFAULT_CUSTOMER_DNI = '00000000'
CURRENT_ACCOUNT_METHOD = 'Cuenta Corriente'
# Ventana aceptada para la fecha local informada por un cliente offline
MAX_OFFLINE_AGE = timedelta(days=7)


class SaleError(Exception):
    """Error de validación de una venta (mensaje apto para mostrar al cajero)."""


def _decimal(value, default='0'):
    try:
        return Decimal(str(value if value not in (None, '') else default))
    except InvalidOperation:
        raise SaleError(f"Valor numérico inválido: {value}")


def _apply_server_promotions(product, qty, unit_price, active_promos, today):
    price = unit_price
    django_day = today.weekday()

    for promo in active_promos:
        is_target = promo.products.filter(id=product.id).exists()
        if not is_target and product.category_id:
            is_target = promo.categories.filter(id=product.category_id).exists()
        if not is_target:
            continue

        if promo.promo_type == 'PERCENT':
            price -= price * (promo.discount_percentage / Decimal('100'))
        elif promo.promo_type == 'DAY_DISCOUNT' and promo.day_of_week == django_day:
            price -= price * (promo.discount_percentage / Decimal('100'))
        elif promo.promo_type == 'FIXED_QTY' and promo.fixed_qty > 0 and qty >= promo.fixed_qty:
            num_packs = qty // promo.fixed_qty
            remainder = qty % promo.fixed_qty
            price = ((num_packs * promo.fixed_price) + (remainder * unit_price)) / qty

    return price.quantize(Decimal('0.01'))


def _price_for_product(product, price_list_id):
    if price_list_id and price_list_id != 'default':
        custom_price = ProductPrice.objects.filter(
            product=product,
            price_list_id=price_list_id,
            price_list__active=True
        ).values_list('price', flat=True).first()
        if custom_price is not None:
            return custom_price
    return product.price


def _item_quantity(item, product):
    """Cantidad vendida: peso en kg para productos fraccionables, unidades para el resto."""
    raw = item.get('weight') if getattr(product, 'unit', None) in ('kg', 'lt', 'litro') and item.get('weight') not in (None, '') else item.get('qty')
    qty = _decimal(raw).quantize(QTY_PLACES)
    if qty <= 0:
        raise SaleError("La cantidad vendida debe ser mayor a cero.")
    if getattr(product, 'is_discrete', False) and (qty % Decimal('1')) != Decimal('0'):
        raise SaleError(f"El producto '{product.name}' se vende por unidad entera y no admite decimales.")
    return qty


def apply_stock_for_sale(product, qty, sale, user):
    """
    SPEC-INV-02 §3.2: descuenta stock sin bloquear nunca la venta.
    `product` debe haberse obtenido con select_for_update() dentro de transaction.atomic().
    Devuelve el ajuste automático generado (Decimal('0') en el caso A).
    """
    current = product.stock
    adjustment = Decimal('0')

    if current >= qty:
        product.stock = current - qty
    else:
        adjustment = qty - max(current, Decimal('0'))
        InventoryMovement.objects.create(
            product=product,
            quantity=adjustment,
            movement_type='AUTO_ADJUST_IN',
            reference=f"Venta #{sale.id}",
            reason=f"Auto-ajuste dinámico en POS por venta #{sale.id} (Stock previo: {current})",
            user=user,
        )
        product.stock = Decimal('0.000')

    product.save()
    InventoryMovement.objects.create(
        product=product,
        quantity=qty,
        movement_type='OUT',
        reference=f"Venta #{sale.id}",
        user=user,
    )
    return adjustment


def parse_payments(payments, payment_method_id=None, default_amount=None):
    """
    SPEC-POS-08 §8.1: acepta `payments: [{"method_id": 1, "amount": 3000, "reference": "..."}]`
    (lista o JSON). Si no hay lista y llega un único `payment_method_id`, se cobra todo con él.
    Devuelve [(PaymentMethod, Decimal amount, reference)].
    """
    if isinstance(payments, str):
        if not payments.strip():
            payments = None
        else:
            try:
                payments = json.loads(payments)
            except ValueError:
                raise SaleError("Datos de pago inválidos.")

    if payments is None:
        if not payment_method_id:
            raise SaleError("Debe seleccionar un método de pago.")
        payments = [{'method_id': payment_method_id, 'amount': default_amount}]
    if not isinstance(payments, list):
        raise SaleError("Datos de pago inválidos.")

    parsed = []
    for entry in payments:
        if not isinstance(entry, dict):
            raise SaleError("Datos de pago inválidos.")
        method_id = entry.get('method_id') or entry.get('payment_method_id') or entry.get('payment_method')
        method = PaymentMethod.objects.filter(id=method_id).first() if method_id else None
        if not method:
            raise SaleError("Método de pago inexistente.")
        amount = _decimal(entry.get('amount')).quantize(MONEY)
        if amount < 0:
            raise SaleError("Los montos de pago no pueden ser negativos.")
        if amount == 0:
            continue
        parsed.append((method, amount, str(entry.get('reference') or '')[:100]))
    return parsed


def _validate_payments_total(payments, total, status):
    paid = sum((amount for _, amount, _ in payments), Decimal('0.00'))
    if status == Sale.STATUS_COMPLETED and paid != total:
        raise SaleError(f"La suma de los pagos (${paid}) debe ser igual al total de la venta (${total}).")
    if status == Sale.STATUS_RESERVED and paid > total:
        raise SaleError(f"La seña (${paid}) no puede superar el total del pedido (${total}).")
    return paid


def _check_current_account(customer, amount):
    if not customer or customer.dni_cuit == DEFAULT_CUSTOMER_DNI:
        raise SaleError("No se puede fiar al Consumidor Final.")
    if customer.limite_credito > 0 and (customer.balance + amount) > customer.limite_credito:
        raise SaleError(f"Límite de crédito excedido. Saldo actual: ${customer.balance}, Límite: ${customer.limite_credito}")


def _register_payments(sale, payments, session, user):
    """Crea los SalePayment y debita en cuenta corriente lo que se fía. Dentro de transaction.atomic()."""
    for method, amount, reference in payments:
        SalePayment.objects.create(
            sale=sale, payment_method=method, amount=amount, reference=reference,
            cash_session=session, user=user,
        )
        if method.name == CURRENT_ACCOUNT_METHOD:
            customer = Customer.objects.select_for_update().get(pk=sale.customer_id)
            _check_current_account(customer, amount)
            customer.balance += amount
            customer.save(update_fields=['balance'])
            CurrentAccount.objects.create(
                customer=customer,
                amount=amount,
                entry_type='DEBT',
                reference=f"Venta #{sale.id}",
                balance_after=customer.balance,
            )


def sale_current_account_amount(sale):
    """Monto fiado de una venta (por SalePayment; ventas previas por Sale.payment_method)."""
    payments = sale.payments.all()
    if payments.exists():
        return sum((p.amount for p in payments if p.payment_method.name == CURRENT_ACCOUNT_METHOD), Decimal('0.00'))
    if sale.payment_method and sale.payment_method.name == CURRENT_ACCOUNT_METHOD:
        return sale.total_amount
    return Decimal('0.00')


def complete_reservation(*, sale, user, payments):
    """
    SPEC-POS-08 §8.2: cobra el saldo de un apartado (RESERVED -> COMPLETED).
    Los pagos impactan en el turno abierto actual.
    """
    with transaction.atomic():
        sale = Sale.objects.select_for_update().get(pk=sale.pk)
        if sale.status != Sale.STATUS_RESERVED:
            raise SaleError(f"La venta #{sale.id} no es un pedido pendiente.")
        session = CashSession.get_active()
        if not session:
            raise SaleError("No hay una caja abierta. Abra la caja para cobrar el pedido.")

        balance = sale.balance_due
        parsed = parse_payments(payments)
        paid = sum((amount for _, amount, _ in parsed), Decimal('0.00'))
        if paid != balance:
            raise SaleError(f"El pago (${paid}) debe cubrir exactamente el saldo pendiente (${balance}).")

        _register_payments(sale, parsed, session, user)
        sale.status = Sale.STATUS_COMPLETED
        sale.save(update_fields=['status', 'is_cancelled'])
        ActivityLog.objects.create(
            user=user,
            action=f"Pedido #{sale.id} entregado y cobrado",
            module="Ventas",
            details=f"Saldo cobrado: ${paid} | Total: ${sale.total_amount}",
        )
    return sale


def cancel_sale(*, sale, user, reason):
    """
    SPEC-AUD-STOCK-06 §6.2.7: Anular una venta revierte exactamente las cantidades
    al Product.stock y crea movimientos SALE_CANCEL_IN dentro de la misma transacción atómica.
    """
    if not reason or not str(reason).strip():
        raise SaleError("Debe indicar el motivo de la anulación.")
    with transaction.atomic():
        sale = Sale.objects.select_for_update().get(pk=sale.pk)
        if sale.is_cancelled:
            raise SaleError("Esta venta ya fue anulada previamente.")

        sale.status = Sale.STATUS_CANCELLED
        sale.is_cancelled = True
        sale.cancel_reason = str(reason).strip()
        sale.cancelled_at = timezone.now()
        sale.cancelled_by = user
        sale.save()

        # Revertir saldo de cuenta corriente por lo fiado en esta venta
        cc_amount = sale_current_account_amount(sale)
        if cc_amount > 0 and sale.customer:
            customer = Customer.objects.select_for_update().get(pk=sale.customer_id)
            customer.balance = max(Decimal('0.00'), customer.balance - cc_amount)
            customer.save(update_fields=['balance'])
            CurrentAccount.objects.create(
                customer=customer,
                amount=cc_amount,
                entry_type='CREDIT',
                reference=f"Anulación Venta #{sale.id}",
                balance_after=customer.balance,
            )

        # Revertir puntos canjeados
        if sale.points_redeemed > 0 and sale.customer:
            sale.customer.points += sale.points_redeemed
            sale.customer.save()

        # Revertir stock de cada producto
        for detail in sale.details.select_related('product').all():
            if detail.product:
                product = Product.objects.select_for_update().get(pk=detail.product_id)
                product.stock += detail.quantity
                product.save()

                InventoryMovement.objects.create(
                    product=product,
                    quantity=detail.quantity,
                    movement_type='SALE_CANCEL_IN',
                    reference=f"Anulación Venta #{sale.id}",
                    reason=f"Anulación Venta #{sale.id}: {sale.cancel_reason}",
                    user=user,
                )

        ActivityLog.objects.create(
            user=user,
            action=f"Anulación Venta #{sale.id}",
            module="Ventas",
            details=f"Motivo: {sale.cancel_reason} | Monto: ${sale.total_amount}"
        )
    return sale


def _parse_client_created_at(value):
    if not value:
        return None
    dt = parse_datetime(str(value))
    if dt is None:
        return None
    if timezone.is_naive(dt):
        dt = timezone.make_aware(dt)
    now = timezone.now()
    if dt > now or now - dt > MAX_OFFLINE_AGE:
        return None
    return dt


def parse_client_uuid(value):
    if not value:
        return None
    try:
        return uuid.UUID(str(value))
    except ValueError:
        raise SaleError("client_uuid inválido.")


def create_sale(*, user, cart, payment_method_id=None, payments=None, customer_id=None, price_list_id=None,
                discount_amount=0, surcharge_amount=0, points_redeemed=0,
                client_uuid=None, client_created_at=None, status=Sale.STATUS_COMPLETED):
    """
    Registra una venta completa (o un apartado con seña). Idempotente por `client_uuid`.
    `payments` (SPEC-POS-08) tiene prioridad; si no llega, se cobra todo con `payment_method_id`.
    Devuelve (sale, created). Lanza SaleError ante datos inválidos.
    """
    client_uuid = parse_client_uuid(client_uuid)
    if client_uuid:
        existing = Sale.objects.filter(client_uuid=client_uuid).first()
        if existing:
            return existing, False

    if isinstance(cart, str):
        try:
            cart = json.loads(cart)
        except ValueError:
            raise SaleError("Datos del carrito inválidos.")
    if not cart:
        raise SaleError("El carrito está vacío.")

    discount_amount = _decimal(discount_amount)
    surcharge_amount = _decimal(surcharge_amount)
    try:
        points_redeemed = int(points_redeemed or 0)
    except (TypeError, ValueError):
        raise SaleError("Cantidad de puntos inválida.")

    if discount_amount < 0 or surcharge_amount < 0:
        raise SaleError("Descuentos y recargos no pueden ser negativos.")
    if points_redeemed < 0:
        raise SaleError("Los puntos a canjear no pueden ser negativos.")
    status = status or Sale.STATUS_COMPLETED
    if status not in (Sale.STATUS_COMPLETED, Sale.STATUS_RESERVED):
        raise SaleError("Estado de venta inválido.")
    # Un apartado puede registrarse sin seña; una venta completada siempre requiere cobro
    has_payment_input = payments not in (None, '') or bool(payment_method_id)
    if has_payment_input:
        # Validación temprana de métodos (los montos se validan contra el total más abajo)
        parse_payments(payments, payment_method_id, default_amount='0')
    elif status == Sale.STATUS_COMPLETED:
        raise SaleError("Debe seleccionar un método de pago.")

    if customer_id:
        customer = Customer.objects.filter(id=customer_id).first()
        if not customer:
            raise SaleError("Cliente inexistente.")
    else:
        customer = Customer.objects.filter(dni_cuit=DEFAULT_CUSTOMER_DNI).first()

    # SPEC-CRM-10 §10.1: sin lista explícita se aplica la del segmento del cliente
    if price_list_id in (None, '') and customer and customer.price_list_id:
        price_list_id = customer.price_list_id

    points_discount = Decimal(points_redeemed)
    if points_redeemed > 0:
        if not customer:
            raise SaleError("Debe seleccionar un cliente para canjear puntos.")
        if customer.points < points_redeemed:
            raise SaleError(f"El cliente no tiene suficientes puntos ({customer.points}).")

    today = timezone.localdate()
    active_promos = Promotion.objects.filter(active=True, start_date__lte=today, end_date__gte=today)

    try:
        with transaction.atomic():
            session = CashSession.get_active()
            if not session:
                raise SaleError("No hay una caja abierta. Abra la caja para registrar ventas.")

            sale_items = []
            total_items = Decimal('0.00')
            total_tax = Decimal('0.00')
            total_promo_disc = Decimal('0.00')

            # Una sola instancia por producto: si el carrito trae varias líneas del mismo producto
            # (p. ej. una venta sincronizada desde el modo offline), cada descuento de stock parte
            # del stock ya descontado por la línea anterior en vez de una copia vieja.
            locked_products = {}
            for item in cart:
                try:
                    product_id = int(item['id'])
                    product = locked_products.get(product_id)
                    if product is None:
                        product = locked_products[product_id] = Product.objects.select_for_update().get(pk=product_id)
                except (KeyError, ValueError, TypeError, Product.DoesNotExist):
                    raise SaleError(f"Producto inexistente en el carrito: {item.get('name') or item.get('id')}")

                qty = _item_quantity(item, product)
                original_price = _price_for_product(product, price_list_id)
                price = _apply_server_promotions(product, qty, original_price, active_promos, today)
                subtotal = (price * qty).quantize(Decimal('0.01'))
                promo_savings = ((original_price - price) * qty).quantize(Decimal('0.01'))
                tax_rate = product.tax_rate
                tax_item = (subtotal - (subtotal / (1 + (tax_rate / 100)))).quantize(Decimal('0.01'))

                total_items += subtotal
                total_tax += tax_item
                total_promo_disc += promo_savings
                sale_items.append((product, qty, price, tax_rate, tax_item, subtotal))

            final_total = total_items - discount_amount - points_discount + surcharge_amount
            if final_total < 0:
                raise SaleError("El total de la venta no puede ser negativo.")

            final_total = final_total.quantize(MONEY)
            parsed_payments = parse_payments(payments, payment_method_id, default_amount=final_total) if has_payment_input else []
            _validate_payments_total(parsed_payments, final_total, status)
            for method, amount, _ in parsed_payments:
                if method.name == CURRENT_ACCOUNT_METHOD:
                    _check_current_account(customer, amount)
            # Método principal: el de mayor monto (compatibilidad con reportes por Sale.payment_method)
            main_method = max(parsed_payments, key=lambda p: p[1])[0] if parsed_payments else (
                PaymentMethod.objects.filter(id=payment_method_id).first() if payment_method_id else None
            )

            sale = Sale.objects.create(
                user=user,
                customer=customer,
                cash_session=session,
                client_uuid=client_uuid,
                total_amount=final_total,
                tax_amount=total_tax,
                discount_amount=discount_amount,
                points_redeemed=points_redeemed,
                points_discount=points_discount,
                promo_discount=total_promo_disc,
                surcharge_amount=surcharge_amount,
                payment_method=main_method,
                status=status,
            )

            created_at = _parse_client_created_at(client_created_at)
            if created_at:
                Sale.objects.filter(pk=sale.pk).update(fecha_hora=created_at)
                sale.fecha_hora = created_at

            if points_redeemed > 0:
                customer.points -= points_redeemed
                customer.save()
                ActivityLog.objects.create(
                    user=user,
                    action=f"Canje de {points_redeemed} puntos - Venta #{sale.id}",
                    module="Fidelización",
                    details=f"Cliente: {customer.full_name}, Descuento: ${points_discount}"
                )

            # Después del canje de puntos: _register_payments relee el cliente con bloqueo
            _register_payments(sale, parsed_payments, session, user)

            for product, qty, price, tax_rate, tax_item, subtotal in sale_items:
                SaleDetail.objects.create(
                    sale=sale,
                    product=product,
                    quantity=qty,
                    peso_kg=qty if product.unit == 'kg' else None,
                    price=price,
                    unit_cost=product.cost_price,
                    tax_rate=tax_rate,
                    tax_amount=tax_item,
                    subtotal=subtotal,
                )
                apply_stock_for_sale(product, qty, sale, user)
    except IntegrityError:
        # Carrera por el mismo client_uuid (p. ej. reintento durante un micro-corte)
        if client_uuid:
            existing = Sale.objects.filter(client_uuid=client_uuid).first()
            if existing:
                return existing, False
        raise

    return sale, True
