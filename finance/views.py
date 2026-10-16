from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.db import IntegrityError, transaction
from django.utils.http import url_has_allowed_host_and_scheme
from core.decorators import manager_required
from django.contrib import messages
from django.utils import timezone
from django.db import models
from decimal import Decimal, InvalidOperation
from .models import CashSession, CashExpense, PaymentMethod
from sales.models import Sale, SaleDetail
from customers.models import Payment
from products.models import InventoryMovement, Purchase

from django.http import HttpResponse
from .utils import generate_cash_report_pdf
import urllib.parse

def session_sales(session):
    """Ventas de una sesión de caja (por FK; las ventas previas a la FK se asocian por cajero y horario)."""
    legacy = models.Q(cash_session__isnull=True, user=session.user, fecha_hora__gte=session.start_date)
    if session.end_date:
        legacy &= models.Q(fecha_hora__lte=session.end_date)
    return Sale.objects.filter(models.Q(cash_session=session) | legacy)


def session_customer_payments(session):
    payments = Payment.objects.filter(user=session.user, date__gte=session.start_date)
    if session.end_date:
        payments = payments.filter(date__lte=session.end_date)
    return payments


def refresh_session_totals(session):
    """Recalcula los totales de la sesión. Debe llamarse dentro de transaction.atomic()."""
    cash_method = PaymentMethod.objects.filter(name='Efectivo').first()
    sales = session_sales(session)

    sales_cash = sales.filter(payment_method=cash_method).aggregate(total=models.Sum('total_amount'))['total'] or Decimal('0.00')
    customer_payments_cash = session_customer_payments(session).filter(
        payment_method=cash_method
    ).aggregate(total=models.Sum('amount'))['total'] or Decimal('0.00')
    expenses_total = session.expenses.aggregate(total=models.Sum('amount'))['total'] or Decimal('0.00')
    digital_sales = sales.filter(payment_method__is_digital=True).aggregate(total=models.Sum('total_amount'))['total'] or Decimal('0.00')

    session.total_sales_cash = sales_cash + customer_payments_cash
    session.total_expenses = expenses_total
    session.total_sales_digital = digital_sales
    session.expected_final_amount = session.initial_amount + session.total_sales_cash - expenses_total
    session.save()


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


@manager_required
def export_cash_report(request, pk):
    session = get_object_or_404(CashSession, pk=pk)

    sales_summary = session_sales(session).values('payment_method__name').annotate(total=models.Sum('total_amount'))
    payments_summary = session_customer_payments(session).values('payment_method__name').annotate(total=models.Sum('amount'))
    expenses = session.expenses.all()

    response = HttpResponse(content_type='application/pdf')
    filename = f"Arqueo_Caja_{session.id}.pdf"
    response['Content-Disposition'] = f'attachment; filename={filename}'

    generate_cash_report_pdf(response, session, sales_summary, payments_summary, expenses,
                             stock_summary=session_stock_summary(session))
    return response

@manager_required
def cash_dashboard(request):
    # SPEC-POS-03: sesión activa global, sin filtrar por fecha ni por usuario
    active_session = CashSession.get_active()
    recent_sessions = CashSession.objects.filter(is_open=False).order_by('-end_date')[:5]

    # Datos de Rentabilidad (Utilidad Bruta) del día actual para todos los usuarios (Vista Gerencial)
    today = timezone.localdate()
    sales_today = Sale.objects.filter(fecha_hora__date=today)

    total_revenue = sales_today.aggregate(total=models.Sum('total_amount'))['total'] or Decimal('0.00')

    # RECALIBRACIÓN: Calcular costo usando el precio de costo CAPTURADO en el momento de la venta
    total_cost = SaleDetail.objects.filter(sale__fecha_hora__date=today).aggregate(
        total=models.Sum(models.F('quantity') * models.F('cost_price_at_sale'), output_field=models.DecimalField())
    )['total'] or Decimal('0.00')

    gross_profit = total_revenue - total_cost
    profit_margin = (gross_profit / total_revenue * 100) if total_revenue > 0 else 0

    other_payments = []
    stock_summary = []
    if active_session:
        with transaction.atomic():
            refresh_session_totals(active_session)

        cash_method = PaymentMethod.objects.filter(name='Efectivo').first()
        cc_method = PaymentMethod.objects.filter(name='Cuenta Corriente').first()
        other_payments = session_sales(active_session).exclude(
            payment_method__in=[cash_method, cc_method]
        ).values('payment_method__name').annotate(total=models.Sum('total_amount'))
        stock_summary = session_stock_summary(active_session)

    return render(request, 'finance/dashboard.html', {
        'session': active_session,
        'session_from_previous_day': bool(active_session) and timezone.localtime(active_session.start_date).date() < today,
        'recent_sessions': recent_sessions,
        'other_payments': other_payments,
        'stock_summary': stock_summary,
        'profit_stats': {
            'revenue': total_revenue,
            'cost': total_cost,
            'profit': gross_profit,
            'margin': profit_margin
        }
    })


def _safe_next(request, default):
    next_url = request.POST.get('next') or request.GET.get('next')
    if next_url and url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
        return next_url
    return default


@login_required
def open_cash(request):
    if request.method == 'POST':
        try:
            amount = Decimal(request.POST.get('amount') or '0.00')
            if amount < 0:
                raise ValueError("El monto inicial no puede ser negativo.")
            with transaction.atomic():
                # Una única caja abierta por comercio: si existe, se opera sobre ella
                existing = CashSession.objects.select_for_update().filter(is_open=True).order_by('-id').first()
                if existing:
                    messages.info(request, f"Ya hay una caja abierta (Caja #{existing.id}). Se continúa operando en ella.")
                else:
                    CashSession.objects.create(user=request.user, initial_amount=amount)
                    messages.success(request, f"¡Caja abierta! Buen turno, {request.user.username}.")
        except IntegrityError:
            # Otra apertura simultánea ganó la carrera (UniqueConstraint unique_open_cash_session)
            messages.info(request, "Ya hay una caja abierta. Se continúa operando en ella.")
        except (ValueError, InvalidOperation) as e:
            messages.error(request, f"Error al abrir caja: {str(e)}")

    return redirect(_safe_next(request, 'finance:cash_dashboard'))

@manager_required
def add_expense(request):
    if request.method == 'POST':
        try:
            amount = Decimal(request.POST.get('amount') or '0.00')
            if amount <= 0:
                raise ValueError("El egreso debe ser mayor a cero.")
            description = request.POST.get('description')
            with transaction.atomic():
                session = CashSession.objects.select_for_update().filter(is_open=True).order_by('-id').first()
                if not session:
                    messages.error(request, "Error: Debes tener una caja abierta para registrar egresos.")
                    return redirect('finance:cash_dashboard')
                CashExpense.objects.create(session=session, amount=amount, description=description)
                refresh_session_totals(session)
            messages.success(request, "Egreso registrado correctamente.")
        except (ValueError, InvalidOperation) as e:
            messages.error(request, f"Error al registrar egreso: {str(e)}")

    return redirect('finance:cash_dashboard')

@login_required
def close_cash(request):
    if request.method == 'POST':
        try:
            real_amount = Decimal(request.POST.get('real_amount') or '0.00')
            if real_amount < 0:
                raise ValueError("El monto final no puede ser negativo.")
            notes = request.POST.get('notes', '')

            with transaction.atomic():
                session = CashSession.objects.select_for_update().filter(is_open=True).order_by('-id').first()
                if not session:
                    messages.error(request, "No se encontró una sesión activa para cerrar.")
                    return redirect(_safe_next(request, 'finance:cash_dashboard'))

                session.end_date = timezone.now()
                refresh_session_totals(session)
                session.real_final_amount = real_amount
                session.notes = notes
                session.is_open = False
                session.save()

            difference = real_amount - session.expected_final_amount
            if difference == 0:
                messages.success(request, "Caja cerrada perfectamente. ¡Todo coincide!")
            elif difference > 0:
                messages.info(request, f"Caja cerrada con SOBRANTE de ${difference}.")
            else:
                messages.warning(request, f"Caja cerrada con FALTANTE de ${abs(difference)}.")

        except (ValueError, InvalidOperation) as e:
            messages.error(request, f"Error al cerrar caja: {str(e)}")

    return redirect(_safe_next(request, 'finance:cash_dashboard'))

@manager_required
def fiscal_reports(request):
    today = timezone.localdate()
    try:
        month = int(request.GET.get('month', today.month))
        year = int(request.GET.get('year', today.year))
    except (TypeError, ValueError):
        month, year = today.month, today.year
    
    # IVA Ventas
    sales = Sale.objects.filter(fecha_hora__month=month, fecha_hora__year=year).order_by('fecha_hora')
    
    # IVA Compras
    purchases = Purchase.objects.filter(date__month=month, date__year=year).order_by('date')

    if 'export' in request.GET:
        import pandas as pd
        response = HttpResponse(content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        response['Content-Disposition'] = f'attachment; filename=Reporte_Fiscal_{month}_{year}.xlsx'
        
        with pd.ExcelWriter(response, engine='openpyxl') as writer:
            # Hoja Ventas
            sales_data = []
            for s in sales:
                neto = s.total_amount - s.tax_amount
                sales_data.append({
                    'Fecha': s.fecha_hora.strftime('%d/%m/%Y'),
                    'Comprobante': f'Ticket #{s.id}',
                    'Cliente': s.customer.full_name if s.customer else 'Consumidor Final',
                    'DNI/CUIT': s.customer.dni_cuit if s.customer else '---',
                    'Neto Gravado': neto,
                    'IVA Liquidado': s.tax_amount,
                    'Total': s.total_amount
                })
            pd.DataFrame(sales_data).to_excel(writer, sheet_name='IVA Ventas', index=False)
            
            purchases_data = []
            for p in purchases:
                total = p.total_amount
                iva = total - (total / Decimal('1.21'))
                neto = total - iva
                purchases_data.append({
                    'Fecha': p.date.strftime('%d/%m/%Y'),
                    'Proveedor': p.supplier.name,
                    'Factura': p.invoice_number,
                    'Neto Gravado': neto,
                    'IVA Crédito': iva,
                    'Total': total
                })
            pd.DataFrame(purchases_data).to_excel(writer, sheet_name='IVA Compras', index=False)
        return response

    return render(request, 'finance/fiscal_reports.html', {
        'sales': sales,
        'purchases': purchases,
        'selected_month': int(month),
        'selected_year': int(year)
    })

@manager_required
def whatsapp_report(request, pk):
    session = get_object_or_404(CashSession, pk=pk)
    fecha = timezone.localtime(session.end_date).strftime('%d/%m/%Y') if session.end_date else timezone.localdate().strftime('%d/%m/%Y')
    efectivo = session.real_final_amount or Decimal('0.00')
    digital = session.total_sales_digital
    total = efectivo + digital
    diferencia = efectivo - session.expected_final_amount
    
    estado_dif = f"Faltante: ${abs(diferencia)}" if diferencia < 0 else (f"Sobrante: ${diferencia}" if diferencia > 0 else "Sin diferencias")
    
    mensaje = f"📊 CIERRE IMPULSO SMART - {fecha} | Total: ${total} | Efec: ${efectivo} | Dig: ${digital} | {estado_dif}"
    link = f"https://wa.me/?text={urllib.parse.quote(mensaje)}"
    return redirect(link)
