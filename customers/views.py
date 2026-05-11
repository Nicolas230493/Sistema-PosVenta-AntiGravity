from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib import messages
from django.db.models import Q
from django.db import transaction
from decimal import Decimal
from django.http import HttpResponse
from sales.customer_utils import generate_customer_statement_pdf
from sales.models import Sale
from .models import Customer, Payment
from .forms import CustomerForm
import urllib.parse

@login_required
def export_statement_pdf(request, pk):
    customer = get_object_or_404(Customer, pk=pk)
    sales = Sale.objects.filter(customer=customer).order_by('date')
    payments = Payment.objects.filter(customer=customer).order_by('date')
    
    # Combinar y ordenar transacciones
    transactions = []
    for s in sales:
        transactions.append({
            'date': s.date,
            'concept': f"Venta #{s.id}",
            'type': 'SALE',
            'amount': s.total_amount
        })
    for p in payments:
        transactions.append({
            'date': p.date,
            'concept': f"Pago #{p.id} ({p.get_payment_method_display()})",
            'type': 'PAYMENT',
            'amount': p.amount
        })
    
    transactions.sort(key=lambda x: x['date'])
    
    response = HttpResponse(content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename=Estado_Cuenta_{customer.full_name}.pdf'
    generate_customer_statement_pdf(response, customer, transactions)
    return response

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

                from .models import CurrentAccount
                CurrentAccount.objects.create(
                    customer=customer,
                    amount=amount,
                    entry_type='CREDIT',
                    reference=f"Pago Directo",
                    balance_after=customer.balance
                )
                
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
def customer_detail(request, pk):
    customer = get_object_or_404(Customer, pk=pk)
    ledger = customer.account_ledger.all()
    return render(request, 'customers/customer_detail.html', {
        'customer': customer,
        'ledger': ledger
    })

@login_required
def whatsapp_reminder(request, pk):
    customer = get_object_or_404(Customer, pk=pk)
    if customer.balance <= 0:
        messages.info(request, "El cliente no tiene saldo deudor.")
        return redirect('customers:customer_detail', pk=pk)
    
    mensaje = f"Hola {customer.full_name}, te saludamos de IMPULSO SMART. Te recordamos que posees un saldo pendiente de ${customer.balance}. ¡Muchas gracias!"
    link = f"https://wa.me/{customer.phone}?text={urllib.parse.quote(mensaje)}"
    return redirect(link)

@staff_member_required
def customer_delete(request, pk):
    customer = get_object_or_404(Customer, pk=pk)
    if customer.dni_cuit == '00000000':
        messages.error(request, "No se puede eliminar al Consumidor Final.")
        return redirect('customers:customer_list')
        
    if request.method == 'POST':
        customer.delete()
        messages.success(request, f"Cliente '{customer.full_name}' eliminado.")
        return redirect('customers:customer_list')
    return render(request, 'customers/customer_confirm_delete.html', {'customer': customer})
