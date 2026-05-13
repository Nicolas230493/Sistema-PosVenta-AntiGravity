from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib import messages
from django.utils import timezone
from django.db import models
from decimal import Decimal
from .models import CashSession, CashExpense, PaymentMethod
from sales.models import Sale, SaleDetail
from customers.models import Payment
from products.models import Purchase

from django.http import HttpResponse
from .utils import generate_cash_report_pdf
import urllib.parse

@login_required
def export_cash_report(request, pk):
    session = get_object_or_404(CashSession, pk=pk)
    
    # Recalcular métricas para el reporte por si hubo cambios manuales
    start = session.start_date
    end = session.end_date if not session.is_open else timezone.now()
    
    sales_summary = Sale.objects.filter(
        user=session.user,
        date__range=[start, end]
    ).values('payment_method__name').annotate(total=models.Sum('total_amount'))
    
    payments_summary = Payment.objects.filter(
        user=session.user,
        date__range=[start, end]
    ).values('payment_method__name').annotate(total=models.Sum('amount'))
    
    expenses = session.expenses.all()
    
    response = HttpResponse(content_type='application/pdf')
    filename = f"Arqueo_Caja_{session.id}.pdf"
    response['Content-Disposition'] = f'attachment; filename={filename}'
    
    # Updated generate_cash_report_pdf to handle the updated summaries
    generate_cash_report_pdf(response, session, sales_summary, payments_summary, expenses)
    return response

@staff_member_required
def cash_dashboard(request):
    # Filtrar por usuario actual para soporte de turnos
    active_session = CashSession.objects.filter(is_open=True, user=request.user).first()
    recent_sessions = CashSession.objects.filter(is_open=False).order_by('-end_date')[:5]
    
    # Datos de Rentabilidad (Utilidad Bruta) del día actual para todos los usuarios (Vista Gerencial)
    today = timezone.localdate()
    sales_today = Sale.objects.filter(date__date=today)
    
    total_revenue = sales_today.aggregate(total=models.Sum('total_amount'))['total'] or Decimal('0.00')
    
    # RECALIBRACIÓN: Calcular costo usando el precio de costo CAPTURADO en el momento de la venta
    total_cost = SaleDetail.objects.filter(sale__date__date=today).aggregate(
        total=models.Sum(models.F('quantity') * models.F('cost_price_at_sale'))
    )['total'] or Decimal('0.00')
    
    gross_profit = total_revenue - total_cost
    profit_margin = (gross_profit / total_revenue * 100) if total_revenue > 0 else 0

    other_payments = []
    if active_session:
        # Métodos de pago
        cash_method = PaymentMethod.objects.filter(name='Efectivo').first()
        cc_method = PaymentMethod.objects.filter(name='Cuenta Corriente').first()

        # 1. Ventas en efectivo de ESTA sesión
        sales_cash = Sale.objects.filter(
            user=request.user,
            date__gte=active_session.start_date, 
            payment_method=cash_method
        ).aggregate(total=models.Sum('total_amount'))['total'] or Decimal('0.00')
        
        # 2. Pagos de deudas de clientes en efectivo en ESTA sesión
        customer_payments_cash = Payment.objects.filter(
            user=request.user,
            date__gte=active_session.start_date,
            payment_method=cash_method
        ).aggregate(total=models.Sum('amount'))['total'] or Decimal('0.00')

        active_session.total_sales_cash = sales_cash + customer_payments_cash
        
        # 3. Egresos registrados en la sesión
        expenses_total = CashExpense.objects.filter(session=active_session).aggregate(total=models.Sum('amount'))['total'] or Decimal('0.00')
        active_session.total_expenses = expenses_total
        
        # 4. Cálculo del esperado
        active_session.expected_final_amount = active_session.initial_amount + active_session.total_sales_cash - expenses_total
        
        # 5. Otras métricas (Tarjetas, Transferencias) para información del cajero
        digital_sales = Sale.objects.filter(
            user=request.user,
            date__gte=active_session.start_date,
            payment_method__is_digital=True
        ).aggregate(total=models.Sum('total_amount'))['total'] or Decimal('0.00')
        
        active_session.total_sales_digital = digital_sales
        active_session.save()
        
        other_payments = Sale.objects.filter(
            user=request.user,
            date__gte=active_session.start_date
        ).exclude(payment_method__in=[cash_method, cc_method]).values('payment_method__name').annotate(total=models.Sum('total_amount'))
        
    return render(request, 'finance/dashboard.html', {
        'session': active_session,
        'recent_sessions': recent_sessions,
        'other_payments': other_payments,
        'profit_stats': {
            'revenue': total_revenue,
            'cost': total_cost,
            'profit': gross_profit,
            'margin': profit_margin
        }
    })

@login_required
def open_cash(request):
    if request.method == 'POST':
        # Evitar múltiples cajas abiertas para el mismo usuario
        if CashSession.objects.filter(user=request.user, is_open=True).exists():
            messages.error(request, "Ya tienes una sesión de caja activa.")
            return redirect('finance:cash_dashboard')
            
        try:
            amount = Decimal(request.POST.get('amount', '0.00'))
            CashSession.objects.create(user=request.user, initial_amount=amount)
            messages.success(request, f"¡Caja abierta! Buen turno, {request.user.username}.")
        except Exception as e:
            messages.error(request, f"Error al abrir caja: {str(e)}")
            
    return redirect('finance:cash_dashboard')

@login_required
def add_expense(request):
    if request.method == 'POST':
        session = CashSession.objects.filter(is_open=True, user=request.user).first()
        if not session:
            messages.error(request, "Error: Debes tener una caja abierta para registrar egresos.")
            return redirect('finance:cash_dashboard')
            
        try:
            amount = Decimal(request.POST.get('amount', '0.00'))
            description = request.POST.get('description')
            CashExpense.objects.create(session=session, amount=amount, description=description)
            messages.success(request, "Egreso registrado correctamente.")
        except Exception as e:
            messages.error(request, f"Error al registrar egreso: {str(e)}")
            
    return redirect('finance:cash_dashboard')

@login_required
def close_cash(request):
    if request.method == 'POST':
        session = CashSession.objects.filter(is_open=True, user=request.user).first()
        if not session:
            messages.error(request, "No se encontró una sesión activa para cerrar.")
            return redirect('finance:cash_dashboard')
            
        try:
            real_amount = Decimal(request.POST.get('real_amount', '0.00'))
            notes = request.POST.get('notes', '')
            
            # Recalcular ventas digitales finales
            digital_sales = Sale.objects.filter(
                user=request.user,
                date__gte=session.start_date,
                payment_method__is_digital=True
            ).aggregate(total=models.Sum('total_amount'))['total'] or Decimal('0.00')
            
            session.total_sales_digital = digital_sales
            session.end_date = timezone.now()
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
                
        except Exception as e:
            messages.error(request, f"Error al cerrar caja: {str(e)}")
            
    return redirect('finance:cash_dashboard')

@staff_member_required
def fiscal_reports(request):
    month = request.GET.get('month', timezone.now().month)
    year = request.GET.get('year', timezone.now().year)
    
    # IVA Ventas
    sales = Sale.objects.filter(date__month=month, date__year=year).order_by('date')
    
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
                    'Fecha': s.date.strftime('%d/%m/%Y'),
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

@login_required
def whatsapp_report(request, pk):
    session = get_object_or_404(CashSession, pk=pk)
    fecha = timezone.localtime(session.end_date).strftime('%d/%m/%Y') if session.end_date else timezone.now().strftime('%d/%m/%Y')
    total = session.real_final_amount + session.total_sales_digital
    efectivo = session.real_final_amount
    digital = session.total_sales_digital
    diferencia = session.real_final_amount - session.expected_final_amount
    
    estado_dif = f"Faltante: ${abs(diferencia)}" if diferencia < 0 else (f"Sobrante: ${diferencia}" if diferencia > 0 else "Sin diferencias")
    
    mensaje = f"📊 CIERRE IMPULSO SMART - {fecha} | Total: ${total} | Efec: ${efectivo} | Dig: ${digital} | {estado_dif}"
    link = f"https://wa.me/?text={urllib.parse.quote(mensaje)}"
    return redirect(link)
