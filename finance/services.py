"""Lógica de caja y rentabilidad (SPEC-FIN-06)."""
from decimal import Decimal

from django.db import models, transaction
from django.utils import timezone

from customers.models import Payment
from products.models import InventoryMovement, StockLoss
from sales.models import Sale, SaleDetail, SalePayment, SaleReturn, SaleReturnDetail

from .models import CashExpense, CashSession, PaymentMethod

ZERO = Decimal('0.00')
CASH_METHOD_NAME = 'Efectivo'


class CashError(Exception):
    """Error de validación de caja (mensaje apto para el usuario)."""


def _sum(queryset, field):
    return queryset.aggregate(total=models.Sum(field))['total'] or ZERO


def session_sales(session):
    """Ventas de una sesión de caja (por FK; las ventas previas a la FK se asocian por cajero y horario)."""
    legacy = models.Q(cash_session__isnull=True, user=session.user, fecha_hora__gte=session.start_date)
    if session.end_date:
        legacy &= models.Q(fecha_hora__lte=session.end_date)
    return Sale.objects.filter(models.Q(cash_session=session) | legacy)


def session_sale_payments(session):
    """
    Cobros ingresados en el turno (SPEC-POS-08): incluye señas y saldos de apartados
    cobrados en este turno aunque la venta se haya creado en otro. Excluye ventas anuladas
    (el dinero se devuelve al anular).
    """
    legacy = models.Q(cash_session__isnull=True, sale__in=session_sales(session))
    return SalePayment.objects.filter(
        models.Q(cash_session=session) | legacy
    ).exclude(sale__status=Sale.STATUS_CANCELLED)


def session_unitemized_sales(session):
    """Ventas completadas sin SalePayment (cargadas fuera del POS): se cobran por Sale.payment_method."""
    return session_sales(session).filter(status=Sale.STATUS_COMPLETED, payments__isnull=True)


def session_customer_payments(session):
    payments = Payment.objects.filter(user=session.user, date__gte=session.start_date)
    if session.end_date:
        payments = payments.filter(date__lte=session.end_date)
    return payments


def compute_expected(session):
    """Efectivo y digital esperados del turno, calculados siempre en el servidor."""
    cash_method = PaymentMethod.objects.filter(name=CASH_METHOD_NAME).first()
    payments = session_sale_payments(session)
    unitemized = session_unitemized_sales(session)
    customer_payments = session_customer_payments(session)

    sales_cash = _sum(payments.filter(payment_method=cash_method), 'amount')
    sales_cash += _sum(unitemized.filter(payment_method=cash_method), 'total_amount')
    customer_payments_cash = _sum(customer_payments.filter(payment_method=cash_method), 'amount')
    expenses_total = _sum(session.expenses.all(), 'amount')
    digital = _sum(payments.filter(payment_method__is_digital=True), 'amount')
    digital += _sum(unitemized.filter(payment_method__is_digital=True), 'total_amount')
    digital += _sum(customer_payments.filter(payment_method__is_digital=True), 'amount')

    total_cash_in = sales_cash + customer_payments_cash
    return {
        'cash_in': total_cash_in,
        'expenses': expenses_total,
        'expected_cash': session.initial_amount + total_cash_in - expenses_total,
        'expected_digital': digital,
    }


def refresh_session_totals(session):
    """Recalcula los totales de la sesión. Debe llamarse dentro de transaction.atomic()."""
    expected = compute_expected(session)
    session.total_sales_cash = expected['cash_in']
    session.total_expenses = expected['expenses']
    session.total_sales_digital = expected['expected_digital']
    session.expected_final_amount = expected['expected_cash']
    session.save()
    return expected


def payment_method_summary(session):
    """Cobros de ventas del turno agrupados por medio de pago."""
    totals = {}
    for row in session_sale_payments(session).values('payment_method__name').annotate(total=models.Sum('amount')):
        totals[row['payment_method__name']] = totals.get(row['payment_method__name'], ZERO) + row['total']
    for row in session_unitemized_sales(session).values('payment_method__name').annotate(total=models.Sum('total_amount')):
        totals[row['payment_method__name']] = totals.get(row['payment_method__name'], ZERO) + row['total']
    return [{'payment_method__name': name, 'total': total} for name, total in sorted(totals.items(), key=lambda i: i[0] or '')]


def session_stock_summary(session):
    """Kilos/unidades vendidos vs. auto-ajustes por desposte (SPEC-INV-02, auditoría de turno)."""
    sales = session_sales(session)
    refs = [f"Venta #{sale_id}" for sale_id in sales.values_list('id', flat=True)]
    sold = SaleDetail.objects.filter(sale__in=sales).values(
        'product_id', 'product__name', 'product__unit'
    ).annotate(total=models.Sum('quantity')).order_by('product__name')
    adjustments = dict(
        InventoryMovement.objects.filter(movement_type='AUTO_ADJUST_IN', reference__in=refs)
        .values_list('product_id').annotate(total=models.Sum('quantity'))
    )
    return [
        {
            'product': row['product__name'] or 'Prod. Eliminado',
            'unit': row['product__unit'] or '',
            'sold': row['total'],
            'auto_adjusted': adjustments.get(row['product_id'], Decimal('0')),
        }
        for row in sold
    ]


def session_forced_adjustments(session):
    """
    SPEC-FIN-06 §6.2 — Panel de Ajustes Forzosos: cada AUTO_ADJUST_IN generado por ventas
    del turno, valuado al precio unitario cobrado en esa venta.
    """
    sales = {f"Venta #{sale_id}": sale_id for sale_id in session_sales(session).values_list('id', flat=True)}
    movements = InventoryMovement.objects.filter(
        movement_type='AUTO_ADJUST_IN', reference__in=list(sales)
    ).select_related('product').order_by('date')

    prices = {
        (row['sale_id'], row['product_id']): row['price']
        for row in SaleDetail.objects.filter(sale_id__in=sales.values()).values('sale_id', 'product_id', 'price')
    }
    rows = []
    total_amount = ZERO
    for mov in movements:
        sale_id = sales[mov.reference]
        price = prices.get((sale_id, mov.product_id), mov.product.price)
        amount = (mov.quantity * price).quantize(Decimal('0.01'))
        total_amount += amount
        rows.append({
            'date': mov.date,
            'sale_id': sale_id,
            'product': mov.product.name,
            'unit': mov.product.unit,
            'quantity': mov.quantity,
            'unit_price': price,
            'amount': amount,
        })
    return rows, total_amount


def close_session(*, user, declared_cash, declared_digital, notes=''):
    """
    SPEC-FIN-06 §6.1: cierre ciego. El esperado se calcula aquí, en el POST de cierre;
    la diferencia queda registrada de forma irreversible.
    """
    if declared_cash is None or declared_digital is None:
        raise CashError("Debe informar el efectivo contado y el total de comprobantes digitales.")
    if declared_cash < 0 or declared_digital < 0:
        raise CashError("Los montos declarados no pueden ser negativos.")

    from core.models import ActivityLog

    with transaction.atomic():
        session = CashSession.objects.select_for_update().filter(is_open=True).order_by('-id').first()
        if not session:
            raise CashError("No se encontró una sesión activa para cerrar.")

        session.end_date = timezone.now()
        expected = refresh_session_totals(session)
        session.expected_cash = expected['expected_cash']
        session.expected_digital = expected['expected_digital']
        session.declared_cash = declared_cash
        session.declared_digital = declared_digital
        session.difference = declared_cash - session.expected_cash
        session.real_final_amount = declared_cash
        session.notes = notes
        session.is_open = False
        session.save()

        ActivityLog.objects.create(
            user=user,
            action=f"Cierre de Caja #{session.id} - {difference_label(session.difference)}",
            module="Caja",
            details=(
                f"Efectivo declarado ${declared_cash} / esperado ${session.expected_cash} | "
                f"Digital declarado ${declared_digital} / esperado ${session.expected_digital}"
            ),
        )
    return session


def difference_label(difference):
    if difference is None:
        return "Sin arqueo"
    if difference == 0:
        return "Sin diferencias"
    if difference > 0:
        return f"Sobrante ${difference}"
    return f"Faltante ${abs(difference)}"


def net_profit(start, end):
    """
    SPEC-FIN-06 §6.2:
    Ganancia Neta = Ventas Netas - CMV - Gastos de Caja - Mermas

    - Ventas Netas: ventas completadas (sin anuladas ni apartados pendientes) menos devoluciones.
    - CMV: cantidad vendida x costo congelado en SaleDetail.unit_cost, neto de devoluciones.
    - Gastos de Caja: egresos de caja (los pagos a proveedores no son gasto: ya están en el CMV).
    - Mermas: StockLoss valuadas al costo registrado en la baja.
    `start` y `end` son datetimes aware (intervalo [start, end)).
    """
    sales = Sale.objects.filter(status=Sale.STATUS_COMPLETED, fecha_hora__gte=start, fecha_hora__lt=end)
    gross_sales = _sum(sales, 'total_amount')

    returns = SaleReturn.objects.filter(sale__in=sales)
    returns_total = _sum(returns, 'total_amount')

    cost_expr = models.ExpressionWrapper(
        models.F('quantity') * models.F('unit_cost'), output_field=models.DecimalField(max_digits=20, decimal_places=5)
    )
    cmv = SaleDetail.objects.filter(sale__in=sales).aggregate(total=models.Sum(cost_expr))['total'] or ZERO

    # Costo de lo devuelto: se toma el unit_cost del renglón de venta original
    returned_cost = ZERO
    for row in SaleReturnDetail.objects.filter(sale_return__in=returns).values('sale_return__sale_id', 'product_id', 'quantity'):
        unit_cost = SaleDetail.objects.filter(
            sale_id=row['sale_return__sale_id'], product_id=row['product_id']
        ).values_list('unit_cost', flat=True).first() or ZERO
        returned_cost += row['quantity'] * unit_cost

    expenses = _sum(
        CashExpense.objects.filter(kind=CashExpense.KIND_EXPENSE, date__gte=start, date__lt=end), 'amount'
    )

    loss_cost = models.ExpressionWrapper(
        models.F('quantity') * models.functions.Coalesce('unit_cost', 'product__cost_price'),
        output_field=models.DecimalField(max_digits=20, decimal_places=5),
    )
    losses = StockLoss.objects.filter(date__gte=start, date__lt=end).aggregate(total=models.Sum(loss_cost))['total'] or ZERO

    q = Decimal('0.01')
    net_sales = (gross_sales - returns_total).quantize(q)
    cmv_net = (cmv - returned_cost).quantize(q)
    expenses = expenses.quantize(q)
    losses = losses.quantize(q)
    gross_profit = net_sales - cmv_net
    profit = gross_profit - expenses - losses
    return {
        'gross_sales': gross_sales,
        'returns': returns_total,
        'net_sales': net_sales,
        'cmv': cmv_net,
        'gross_profit': gross_profit,
        'expenses': expenses,
        'losses': losses,
        'net_profit': profit,
        'net_margin': (profit / net_sales * 100).quantize(q) if net_sales > 0 else ZERO,
        'sales_count': sales.count(),
    }
