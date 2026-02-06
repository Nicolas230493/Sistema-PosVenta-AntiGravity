from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required
from django.db.models import Sum
from .models import CashMovement, DailyClose
from .forms import CashMovementForm

@login_required
def cash_dashboard(request):
    movements = CashMovement.objects.all().order_by('-date')[:50]
    total_in = CashMovement.objects.filter(type='IN').aggregate(Sum('amount'))['amount__sum'] or 0
    total_out = CashMovement.objects.filter(type='OUT').aggregate(Sum('amount'))['amount__sum'] or 0
    balance = total_in - total_out
    
    if request.method == 'POST':
        form = CashMovementForm(request.POST)
        if form.is_valid():
            movement = form.save(commit=False)
            movement.user = request.user
            movement.save()
            return redirect('cash_dashboard')
    else:
        form = CashMovementForm()
        
    return render(request, 'finance/dashboard.html', {
        'movements': movements,
        'total_in': total_in,
        'total_out': total_out,
        'balance': balance,
        'form': form
    })
