from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.utils import timezone
from django.db import models
from decimal import Decimal
from .models import CashSession, CashExpense
from sales.models import Sale, SaleDetail
from customers.models import Payment

@login_required
def cash_dashboard(request):
    # Filtrar por usuario actual para soporte de turnos
    active_session = CashSession.objects.filter(is_open=True, user=request.user).first()
    
    # Datos de Rentabilidad (Utilidad Bruta) del día actual para todos los usuarios (Vista Gerencial)
    today = timezone.localdate()
    sales_today = Sale.objects.filter(date__date=today)
    
    total_revenue = sales_today.aggregate(total=models.Sum('total_amount'))['total'] or Decimal('0.00')
    
    # Calcular costo de lo vendido hoy
    # Usamos F() para multiplicar cantidad por precio de costo de cada producto en el momento de la consulta
    total_cost = SaleDetail.objects.filter(sale__date__date=today).aggregate(
        total=models.Sum(models.F('quantity') * models.F('product__cost_price'))
    )['total'] or Decimal('0.00')
    
    gross_profit = total_revenue - total_cost
    profit_margin = (gross_profit / total_revenue * 100) if total_revenue > 0 else 0

    if active_session:
        # 1. Ventas en efectivo de ESTA sesión
        sales_cash = Sale.objects.filter(
            user=request.user,
            date__gte=active_session.start_date, 
            payment_method='CASH'
        ).aggregate(total=models.Sum('total_amount'))['total'] or Decimal('0.00')
        
        # 2. Pagos de deudas de clientes en efectivo en ESTA sesión
        customer_payments_cash = Payment.objects.filter(
            user=request.user,
            date__gte=active_session.start_date,
            payment_method='CASH'
        ).aggregate(total=models.Sum('amount'))['total'] or Decimal('0.00')

        active_session.total_sales_cash = sales_cash + customer_payments_cash
        
        # 3. Egresos registrados en la sesión
        expenses = CashExpense.objects.filter(session=active_session).aggregate(total=models.Sum('amount'))['total'] or Decimal('0.00')
        active_session.total_expenses = expenses
        
        # 4. Cálculo del esperado
        active_session.expected_final_amount = active_session.initial_amount + active_session.total_sales_cash - expenses
        active_session.save()
        
        # 5. Otras métricas (Tarjetas, Transferencias) para información del cajero
        other_payments = Sale.objects.filter(
            user=request.user,
            date__gte=active_session.start_date
        ).exclude(payment_method__in=['CASH', 'CC']).values('payment_method').annotate(total=models.Sum('total_amount'))
        
    else:
        other_payments = []
        
    return render(request, 'finance/dashboard.html', {
        'session': active_session,
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
