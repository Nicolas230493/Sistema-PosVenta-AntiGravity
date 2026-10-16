"""Lógica de negocio de ventas compartida por el POS online y la sincronización offline."""
import json
import uuid
from datetime import timedelta
from decimal import Decimal, InvalidOperation

from django.db import IntegrityError, transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from core.models import ActivityLog
from customers.models import Customer
from finance.models import CashSession, PaymentMethod
from products.models import InventoryMovement, Product, ProductPrice

from .models import Promotion, Sale, SaleDetail

QTY_PLACES = Decimal('0.001')
DEFAULT_CUSTOMER_DNI = '00000000'
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
    raw = item.get('weight') if product.unit == 'kg' and item.get('weight') not in (None, '') else item.get('qty')
    qty = _decimal(raw).quantize(QTY_PLACES)
    if qty <= 0:
        raise SaleError("La cantidad vendida debe ser mayor a cero.")
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


def create_sale(*, user, cart, payment_method_id, customer_id=None, price_list_id=None,
                discount_amount=0, surcharge_amount=0, points_redeemed=0,
                client_uuid=None, client_created_at=None):
    """
    Registra una venta completa. Idempotente por `client_uuid`.
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
    if not payment_method_id:
        raise SaleError("Debe seleccionar un método de pago.")

    payment_method = PaymentMethod.objects.filter(id=payment_method_id).first()
    if not payment_method:
        raise SaleError("Método de pago inexistente.")

    if customer_id:
        customer = Customer.objects.filter(id=customer_id).first()
        if not customer:
            raise SaleError("Cliente inexistente.")
    else:
        customer = Customer.objects.filter(dni_cuit=DEFAULT_CUSTOMER_DNI).first()

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

            for item in cart:
                try:
                    product = Product.objects.select_for_update().get(pk=item['id'])
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

            if payment_method.name == 'Cuenta Corriente' and customer:
                if customer.limite_credito > 0:  # 0 significa sin límite o sin crédito habilitado
                    if (customer.balance + final_total) > customer.limite_credito:
                        raise SaleError(f"Límite de crédito excedido. Saldo actual: ${customer.balance}, Límite: ${customer.limite_credito}")
                elif customer.dni_cuit == DEFAULT_CUSTOMER_DNI:
                    raise SaleError("No se puede fiar al Consumidor Final.")

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
                payment_method=payment_method,
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

            for product, qty, price, tax_rate, tax_item, subtotal in sale_items:
                SaleDetail.objects.create(
                    sale=sale,
                    product=product,
                    quantity=qty,
                    peso_kg=qty if product.unit == 'kg' else None,
                    price=price,
                    cost_price_at_sale=product.cost_price,
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
