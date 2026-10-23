from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib import messages
from django.db import transaction
from .models import Supplier
from .forms import SupplierForm, SupplierRemitoEntryForm
from decimal import Decimal, InvalidOperation
from django.utils import timezone
from django.views.decorators.http import require_POST
from core.decorators import manager_required
from .models import SupplierAccountMovement
from .services import SupplierError, receive_stock, register_supplier_payment

@login_required
def supplier_list(request):
    suppliers = Supplier.objects.all()
    return render(request, 'suppliers/supplier_list.html', {'suppliers': suppliers})

@login_required
def supplier_create(request):
    if request.method == 'POST':
        form = SupplierForm(request.POST)
        if form.is_valid():
            form.save()
            return redirect('suppliers:supplier_list')
    else:
        form = SupplierForm()
    return render(request, 'suppliers/supplier_form.html', {'form': form, 'title': 'Nuevo Proveedor'})

@login_required
def supplier_update(request, pk):
    supplier = get_object_or_404(Supplier, pk=pk)
    if request.method == 'POST':
        form = SupplierForm(request.POST, instance=supplier)
        if form.is_valid():
            form.save()
            return redirect('suppliers:supplier_list')
    else:
        form = SupplierForm(instance=supplier)
    return render(request, 'suppliers/supplier_form.html', {'form': form, 'title': 'Editar Proveedor'})

@staff_member_required
def supplier_delete(request, pk):
    supplier = get_object_or_404(Supplier, pk=pk)
    if request.method == 'POST':
        name = supplier.name
        supplier.delete()
        messages.success(request, f"Proveedor '{name}' eliminado.")
        return redirect('suppliers:supplier_list')
    return render(request, 'suppliers/supplier_confirm_delete.html', {'supplier': supplier})

@login_required
def supplier_remito_entry(request, pk=None):
    """SPEC-AUD-STOCK-06 §6.2.5: Carga de mercadería de proveedor o remito incrementa stock y genera PURCHASE_IN."""
    initial = {}
    if pk:
        supplier = get_object_or_404(Supplier, pk=pk)
        initial['supplier'] = supplier

    if request.method == 'POST':
        form = SupplierRemitoEntryForm(request.POST)
        if form.is_valid():
            supplier = form.cleaned_data['supplier']
            product = form.cleaned_data['product']
            quantity = form.cleaned_data['quantity']
            remito = form.cleaned_data.get('remito_number') or 'S/N'
            cost = form.cleaned_data.get('cost_price')
            notes = form.cleaned_data.get('notes') or f"Entrada por Remito {remito}"

            with transaction.atomic():
                # SPEC-SUP-07 §7.1: el costo informado se promedia (CPP) con el stock existente
                prod = receive_stock(
                    product=product,
                    quantity=quantity,
                    unit_cost=cost,
                    reference=f"Remito {remito} - {supplier.name}",
                    reason=notes,
                    user=request.user,
                )
                if not prod.supplier:
                    prod.supplier = supplier
                    prod.save(update_fields=['supplier'])

            messages.success(request, f"Se ingresaron {quantity} de '{prod.name}' desde {supplier.name} (Remito {remito}). Nuevo stock: {prod.stock}")
            return redirect('suppliers:supplier_list')
    else:
        form = SupplierRemitoEntryForm(initial=initial)

    return render(request, 'suppliers/supplier_remito_entry.html', {'form': form, 'title': 'Carga de Remito / Mercadería'})


@manager_required
def supplier_account(request, pk):
    """SPEC-SUP-07 §7.2: cuenta corriente del proveedor (deudas por mercadería y pagos)."""
    supplier = get_object_or_404(Supplier, pk=pk)
    movements = supplier.account_movements.select_related('purchase_order', 'user')
    pending_debits = movements.filter(movement_type=SupplierAccountMovement.DEBIT, due_date__isnull=False).order_by('due_date')
    return render(request, 'suppliers/supplier_account.html', {
        'supplier': supplier,
        'movements': movements,
        'pending_debits': pending_debits[:10] if supplier.balance > 0 else [],
        'sources': SupplierAccountMovement.SOURCES,
        'today': timezone.localdate(),
    })


@manager_required
@require_POST
def supplier_payment(request, pk):
    supplier = get_object_or_404(Supplier, pk=pk)
    try:
        try:
            amount = Decimal(request.POST.get('amount') or '')
        except InvalidOperation:
            raise SupplierError("Monto inválido.")
        register_supplier_payment(
            supplier=supplier,
            amount=amount,
            source=request.POST.get('source'),
            user=request.user,
            notes=request.POST.get('notes', '')[:255],
        )
        messages.success(request, f"Pago de ${amount} a {supplier.name} registrado.")
    except SupplierError as e:
        messages.error(request, str(e))
    return redirect('suppliers:supplier_account', pk=pk)
