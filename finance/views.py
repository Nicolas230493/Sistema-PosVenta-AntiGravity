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
from products.models import Purchase

from django.http import HttpResponse
from core.decorators import es_arqueo_ciego, es_encargado_o_admin
from .services import (
    CashError, close_session, difference_label, net_profit, payment_method_summary,
    refresh_session_totals, session_customer_payments, session_forced_adjustments,
    session_stock_summary,
)
from .utils import generate_cash_report_pdf
from datetime import datetime, time, timedelta
import urllib.parse

def _blind_open_session(user, session):
    """SPEC-UI-12 §12.1: mientras la caja está abierta, quien no es Administrador no ve el esperado."""
    return session.is_open and es_arqueo_ciego(user)


def _deny_blind_report(request):
    messages.error(request, "Arqueo ciego: el reporte de la caja abierta solo lo ve el Administrador. Estará disponible al cerrar.")
    return redirect('finance:cash_dashboard')


@manager_required
def export_cash_report(request, pk):
    session = get_object_or_404(CashSession, pk=pk)
    if _blind_open_session(request.user, session):
        return _deny_blind_report(request)

    payments_summary = session_customer_payments(session).values('payment_method__name').annotate(total=models.Sum('amount'))
    expenses = session.expenses.all()
    adjustments, adjustments_total = session_forced_adjustments(session)

    response = HttpResponse(content_type='application/pdf')
    filename = f"Arqueo_Caja_{session.id}.pdf"
    response['Content-Disposition'] = f'attachment; filename={filename}'

    generate_cash_report_pdf(response, session, payment_method_summary(session), payments_summary, expenses,
                             stock_summary=session_stock_summary(session),
                             forced_adjustments=adjustments, forced_adjustments_total=adjustments_total)
    return response


@manager_required
def cash_session_report(request, pk):
    """SPEC-FIN-06: reporte de cierre con diferencia destacada y panel de Ajustes Forzosos."""
    session = get_object_or_404(CashSession, pk=pk)
    if session.is_open:
        with transaction.atomic():
            refresh_session_totals(session)
    adjustments, adjustments_total = session_forced_adjustments(session)
    blind = _blind_open_session(request.user, session)
    return render(request, 'finance/session_report.html', {
        'session': session,
        'blind': blind,
        'difference_label': difference_label(session.difference),
        'payment_summary': [] if blind else payment_method_summary(session),
        'expenses': session.expenses.all(),
        'adjustments': adjustments,
        'adjustments_total': adjustments_total,
        'stock_summary': session_stock_summary(session),
    })

@manager_required
def cash_dashboard(request):
    # SPEC-POS-03: sesión activa global, sin filtrar por fecha ni por usuario
    active_session = CashSession.get_active()
    recent_sessions = CashSession.objects.filter(is_open=False).order_by('-end_date')[:5]

    # Datos de Rentabilidad (Utilidad Bruta) del día actual para todos los usuarios (Vista Gerencial)
    today = timezone.localdate()
    # Solo ventas concretadas (sin anuladas ni pedidos con seña pendientes)
    sales_today = Sale.objects.filter(fecha_hora__date=today, status=Sale.STATUS_COMPLETED)

    total_revenue = sales_today.aggregate(total=models.Sum('total_amount'))['total'] or Decimal('0.00')

    # Costo congelado en SaleDetail.unit_cost al momento de la venta
    total_cost = SaleDetail.objects.filter(sale__in=sales_today).aggregate(
        total=models.Sum(models.F('quantity') * models.F('unit_cost'), output_field=models.DecimalField())
    )['total'] or Decimal('0.00')

    gross_profit = total_revenue - total_cost
    profit_margin = (gross_profit / total_revenue * 100) if total_revenue > 0 else 0

    # SPEC-UI-12 §12.1: el Encargado opera la caja a ciegas. No recibe el esperado ni sus
    # componentes (ventas en efectivo/digitales del turno, ventas del día) para no poder deducirlo.
    blind = es_arqueo_ciego(request.user)

    other_payments = []
    stock_summary = []
    adjustments = []
    if active_session:
        with transaction.atomic():
            refresh_session_totals(active_session)

    if active_session and not blind:
        other_payments = [
            row for row in payment_method_summary(active_session)
            if row['payment_method__name'] not in ('Efectivo', 'Cuenta Corriente')
        ]
    if active_session:
        stock_summary = session_stock_summary(active_session)
        adjustments, _ = session_forced_adjustments(active_session)

    return render(request, 'finance/dashboard.html', {
        'session': active_session,
        'session_from_previous_day': bool(active_session) and timezone.localtime(active_session.start_date).date() < today,
        'recent_sessions': recent_sessions,
        'other_payments': other_payments,
        'stock_summary': stock_summary,
        'forced_adjustments': adjustments,
        'blind': blind,
        # Utilidad bruta del día: solo Administrador (misma política que el middleware de roles)
        'profit_stats': None if blind else {
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

def _parse_amount(value, label):
    try:
        return Decimal(str(value).strip())
    except (InvalidOperation, AttributeError):
        raise CashError(f"Monto inválido en '{label}'.")


@login_required
def close_cash(request):
    """
    SPEC-FIN-06 §6.1 / SPEC-UI-12 §12.1 — Cierre ciego: quien no es Administrador no ve el
    efectivo esperado (ni siquiera en el HTML); declara lo contado y el servidor calcula
    esperado y diferencia en el POST.
    """
    session = CashSession.get_active()
    blind = es_arqueo_ciego(request.user)

    if request.method == 'POST':
        try:
            declared_cash = request.POST.get('declared_cash', request.POST.get('real_amount'))
            declared_digital = request.POST.get('declared_digital')
            if declared_cash in (None, '') or declared_digital in (None, ''):
                raise CashError("Debe informar el efectivo contado y el total de comprobantes digitales (0 si no hubo).")
            session = close_session(
                user=request.user,
                declared_cash=_parse_amount(declared_cash, 'Efectivo contado'),
                declared_digital=_parse_amount(declared_digital, 'Comprobantes digitales'),
                notes=request.POST.get('notes', ''),
            )
        except CashError as e:
            messages.error(request, f"Error al cerrar caja: {e}")
            return redirect(_safe_next(request, 'finance:close_cash'))

        # La diferencia se revela recién con el turno cerrado de forma irreversible
        label = difference_label(session.difference)
        if session.difference == 0:
            messages.success(request, f"Caja #{session.id} cerrada. {label}.")
        elif session.difference > 0:
            messages.info(request, f"Caja #{session.id} cerrada con {label}.")
        else:
            messages.warning(request, f"Caja #{session.id} cerrada con {label}.")

        if es_encargado_o_admin(request.user):
            return redirect('finance:cash_session_report', pk=session.pk)
        return redirect(_safe_next(request, 'dashboard'))

    if not session:
        messages.info(request, "No hay una caja abierta.")
        return redirect('dashboard')

    adjustments, adjustments_total = session_forced_adjustments(session)
    context = {
        'session': session,
        'blind': blind,
        'next': _safe_next(request, ''),
        'adjustments': adjustments,
        'adjustments_total': adjustments_total,
    }
    if not blind:
        with transaction.atomic():
            context['expected'] = refresh_session_totals(session)
    return render(request, 'finance/close_cash.html', context)


@manager_required
def profit_report(request):
    """SPEC-FIN-06 §6.2: Ganancia Neta = Ventas Netas - CMV - Gastos de Caja - Mermas."""
    today = timezone.localdate()
    try:
        date_from = datetime.strptime(request.GET.get('from', ''), '%Y-%m-%d').date()
    except ValueError:
        date_from = today.replace(day=1)
    try:
        date_to = datetime.strptime(request.GET.get('to', ''), '%Y-%m-%d').date()
    except ValueError:
        date_to = today
    if date_to < date_from:
        date_from, date_to = date_to, date_from

    tz = timezone.get_current_timezone()
    start = datetime.combine(date_from, time.min, tzinfo=tz)
    end = datetime.combine(date_to + timedelta(days=1), time.min, tzinfo=tz)
    return render(request, 'finance/profit_report.html', {
        'report': net_profit(start, end),
        'date_from': date_from,
        'date_to': date_to,
    })


@manager_required
def fiscal_reports(request):
    today = timezone.localdate()
    try:
        month = int(request.GET.get('month', today.month))
        year = int(request.GET.get('year', today.year))
    except (TypeError, ValueError):
        month, year = today.month, today.year
    
    # IVA Ventas
    # IVA Ventas: solo ventas concretadas; las anuladas y los pedidos con seña pendientes no se declaran
    sales = Sale.objects.filter(
        fecha_hora__month=month, fecha_hora__year=year, status=Sale.STATUS_COMPLETED
    ).order_by('fecha_hora')
    
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
    if _blind_open_session(request.user, session):
        return _deny_blind_report(request)
    fecha = timezone.localtime(session.end_date).strftime('%d/%m/%Y') if session.end_date else timezone.localdate().strftime('%d/%m/%Y')
    efectivo = session.declared_cash if session.declared_cash is not None else (session.real_final_amount or Decimal('0.00'))
    digital = session.declared_digital if session.declared_digital is not None else session.total_sales_digital
    total = efectivo + digital
    estado_dif = difference_label(session.difference)
    adjustments, adjustments_total = session_forced_adjustments(session)

    mensaje = f"📊 CIERRE IMPULSO SMART - Caja #{session.id} - {fecha} | Total: ${total} | Efec: ${efectivo} | Dig: ${digital} | {estado_dif}"
    if adjustments:
        mensaje += f" | Ajustes forzosos: {len(adjustments)} (${adjustments_total})"
    link = f"https://wa.me/?text={urllib.parse.quote(mensaje)}"
    return redirect(link)
