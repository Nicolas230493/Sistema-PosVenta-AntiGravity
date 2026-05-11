from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib import messages
from .models import Supplier
from .forms import SupplierForm

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
