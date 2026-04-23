from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.utils import timezone
from django.db import models
from .models import CashSession, CashExpense
from sales.models import Sale

@login_required
def cash_dashboard(request):
    active_session = CashSession.objects.filter(is_open=True).first()
    
    if active_session:
        # Calcular ventas en efectivo desde la apertura
        sales_cash = Sale.objects.filter(
            date__gte=active_session.start_date, 
            payment_method='CASH'
        ).aggregate(total=models.Sum('total_amount'))['total'] or 0
        
        active_session.total_sales_cash = sales_cash
        
        # Calcular egresos
        expenses = CashExpense.objects.filter(session=active_session).aggregate(total=models.Sum('amount'))['total'] or 0
        active_session.total_expenses = expenses
        
        active_session.expected_final_amount = active_session.initial_amount + sales_cash - expenses
        active_session.save()
        
    return render(request, 'finance/dashboard.html', {'session': active_session})

@login_required
def open_cash(request):
    if request.method == 'POST':
        amount = request.POST.get('amount')
        CashSession.objects.create(user=request.user, initial_amount=amount)
        messages.success(request, "Caja abierta correctamente.")
    return redirect('finance:cash_dashboard')

@login_required
def add_expense(request):
    if request.method == 'POST':
        session = CashSession.objects.filter(is_open=True).first()
        if not session:
            messages.error(request, "No hay una caja abierta.")
            return redirect('finance:cash_dashboard')
            
        amount = request.POST.get('amount')
        desc = request.POST.get('description')
        CashExpense.objects.create(session=session, amount=amount, description=desc)
        messages.success(request, "Egreso registrado.")
    return redirect('finance:cash_dashboard')

@login_required
def close_cash(request):
    if request.method == 'POST':
        session = CashSession.objects.filter(is_open=True).first()
        real_amount = request.POST.get('real_amount')
        notes = request.POST.get('notes')
        
        session.end_date = timezone.now()
        session.real_final_amount = real_amount
        session.notes = notes
        session.is_open = False
        session.save()
        
        messages.warning(request, f"Caja cerrada. Diferencia: {float(real_amount) - float(session.expected_final_amount)}")
    return redirect('finance:cash_dashboard')
