from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.db.models import Q
from .models import Product, InventoryMovement
from .forms import ProductForm, InventoryMovementForm

@login_required
def product_list(request):
    query = request.GET.get('q')
    if query:
        products = Product.objects.filter(Q(name__icontains=query) | Q(description__icontains=query))
    else:
        products = Product.objects.all()
    return render(request, 'products/product_list.html', {'products': products})

@login_required
def product_create(request):
    if request.method == 'POST':
        form = ProductForm(request.POST)
        if form.is_valid():
            form.save()
            return redirect('product_list')
    else:
        form = ProductForm()
    return render(request, 'products/product_form.html', {'form': form, 'title': 'Nuevo Producto'})

@login_required
def product_update(request, pk):
    product = get_object_or_404(Product, pk=pk)
    if request.method == 'POST':
        form = ProductForm(request.POST, instance=product)
        if form.is_valid():
            form.save()
            return redirect('product_list')
    else:
        form = ProductForm(instance=product)
    return render(request, 'products/product_form.html', {'form': form, 'title': 'Editar Producto'})

@login_required
def product_delete(request, pk):
    product = get_object_or_404(Product, pk=pk)
    if request.method == 'POST':
        product.delete()
        return redirect('product_list')
    return render(request, 'products/product_confirm_delete.html', {'product': product})

@login_required
def inventory_history(request):
    movements = InventoryMovement.objects.all()
    return render(request, 'products/inventory_history.html', {'movements': movements})

@login_required
def stock_entry(request):
    if request.method == 'POST':
        form = InventoryMovementForm(request.POST)
        if form.is_valid():
            movement = form.save(commit=False)
            movement.user = request.user
            movement.movement_type = 'IN' # Force IN for this view
            
            # Update stock
            product = movement.product
            product.stock += movement.quantity
            product.save()
            
            movement.save()
            return redirect('inventory_history')
    else:
        form = InventoryMovementForm()
    return render(request, 'products/stock_entry.html', {'form': form, 'title': 'Entrada de Stock'})

