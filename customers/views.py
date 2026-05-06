from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.db.models import Q
from django.db import transaction
from decimal import Decimal
from .models import Customer, Payment
from .forms import CustomerForm

@login_required
def customer_payment(request, pk):
    customer = get_object_or_404(Customer, pk=pk)
    if request.method == 'POST':
        amount = request.POST.get('amount')
        method = request.POST.get('payment_method')
        notes = request.POST.get('notes')
        
        try:
            amount = Decimal(amount)
            if amount <= 0:
                raise Exception("El monto debe ser mayor a cero.")
            
            with transaction.atomic():
                # Crear el registro de pago
                Payment.objects.create(
                    customer=customer,
                    amount=amount,
                    payment_method=method,
                    notes=notes,
                    user=request.user
                )
                
                # Descontar del saldo del cliente
                customer.balance -= amount
                customer.save()
                
                messages.success(request, f"Pago de ${amount} registrado correctamente para {customer.full_name}.")
                return redirect('customers:customer_list')
        except Exception as e:
            messages.error(request, f"Error al registrar pago: {str(e)}")
            
    return render(request, 'customers/customer_payment.html', {'customer': customer})

@login_required
def customer_list(request):
    query = request.GET.get('q')
    customers = Customer.objects.all()
    if query:
        customers = customers.filter(
            Q(full_name__icontains=query) | 
            Q(dni_cuit__icontains=query)
        )
    return render(request, 'customers/customer_list.html', {'customers': customers})

@login_required
def customer_create(request):
    if request.method == 'POST':
        form = CustomerForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, "Cliente creado correctamente.")
            return redirect('customers:customer_list')
    else:
        form = CustomerForm()
    return render(request, 'customers/customer_form.html', {'form': form, 'title': 'Nuevo Cliente'})

@login_required
def customer_update(request, pk):
    customer = get_object_or_404(Customer, pk=pk)
    # Evitar edición del Consumidor Final si es necesario (opcional)
    if request.method == 'POST':
        form = CustomerForm(request.POST, instance=customer)
        if form.is_valid():
            form.save()
            messages.success(request, "Cliente actualizado.")
            return redirect('customers:customer_list')
    else:
        form = CustomerForm(instance=customer)
    return render(request, 'customers/customer_form.html', {'form': form, 'title': 'Editar Cliente'})

@login_required
def customer_delete(request, pk):
    customer = get_object_or_404(Customer, pk=pk)
    if customer.dni_cuit == '00000000':
        messages.error(request, "No se puede eliminar al Consumidor Final.")
        return redirect('customers:customer_list')
        
    if request.method == 'POST':
        customer.delete()
        messages.success(request, "Cliente eliminado.")
        return redirect('customers:customer_list')
    return render(request, 'customers/customer_confirm_delete.html', {'customer': customer})
