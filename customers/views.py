from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib import messages
from django.db.models import Q
from django.db import transaction
from decimal import Decimal
from django.http import HttpResponse
from django.utils import timezone
from sales.customer_utils import generate_customer_statement_pdf
from finance.models import PaymentMethod
from .models import Customer, Payment
from .forms import CustomerForm
import urllib.parse

STATEMENT_WHATSAPP_ENTRIES = 15


def statement_entries(customer):
    """
    SPEC-CRM-10 §10.2: detalle cronológico de la cuenta corriente (CurrentAccount):
    compras fiadas (DEBT), entregas parciales/pagos (CREDIT) y saldo resultante.
    """
    return list(customer.account_ledger.order_by('date', 'id'))


@login_required
def export_statement_pdf(request, pk):
    customer = get_object_or_404(Customer, pk=pk)
    response = HttpResponse(content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename=Estado_Cuenta_{customer.id}.pdf'
    generate_customer_statement_pdf(response, customer, statement_entries(customer))
    return response


@login_required
def whatsapp_statement(request, pk):
    """Estado de cuenta resumido por WhatsApp con los últimos movimientos y el saldo actual."""
    customer = get_object_or_404(Customer, pk=pk)
    phone = ''.join(ch for ch in (customer.phone or '') if ch.isdigit())
    if not phone:
        messages.error(request, "El cliente no tiene un teléfono registrado.")
        return redirect('customers:customer_detail', pk=pk)

    entries = statement_entries(customer)
    recent = entries[-STATEMENT_WHATSAPP_ENTRIES:]
    lines = [f"*Estado de cuenta - {customer.full_name}*"]
    if len(entries) > len(recent):
        lines.append(f"(últimos {len(recent)} de {len(entries)} movimientos)")
    for entry in recent:
        fecha = timezone.localtime(entry.date).strftime('%d/%m/%Y')
        if entry.entry_type == 'DEBT':
            concepto = f"Compra fiada {entry.reference or ''}".strip()
            monto = f"+${entry.amount}"
        else:
            concepto = f"Entrega/Pago {entry.reference or ''}".strip()
            monto = f"-${entry.amount}"
        lines.append(f"{fecha} | {concepto}: {monto} | Saldo ${entry.balance_after}")
    lines.append(f"*SALDO DEUDOR ACTUAL: ${customer.balance}*")
    lines.append("¡Muchas gracias!")
    return redirect(f"https://wa.me/{phone}?text={urllib.parse.quote(chr(10).join(lines))}")


@login_required
def customer_payment(request, pk):
    customer = get_object_or_404(Customer, pk=pk)
    payment_methods = PaymentMethod.objects.filter(active=True).exclude(name='Cuenta Corriente')
    
    if request.method == 'POST':
        amount = request.POST.get('amount')
        method_id = request.POST.get('payment_method')
        notes = request.POST.get('notes')
        
        try:
            amount = Decimal(amount)
            if amount <= 0:
                raise Exception("El monto debe ser mayor a cero.")
            
            method = PaymentMethod.objects.get(id=method_id)
            
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
