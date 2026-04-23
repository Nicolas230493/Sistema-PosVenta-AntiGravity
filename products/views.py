from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.db.models import Q
from .models import Product, InventoryMovement
from .forms import ProductForm, InventoryMovementForm

from django.db.models import Q, F
from django.utils import timezone

@login_required
def product_list(request):
    query = request.GET.get('q')
    status_filter = request.GET.get('status')
    
    # Optimizamos con select_related para evitar N+1
    products = Product.objects.select_related('supplier').all()
    
    if query:
        products = products.filter(
            Q(name__icontains=query) | 
            Q(description__icontains=query) |
            Q(sku__icontains=query)
        )
    
    if status_filter == 'danger':
        products = products.filter(stock__lte=0)
    elif status_filter == 'warning':
        products = products.filter(stock__gt=0, stock__lte=F('min_stock'))
    elif status_filter == 'success':
        products = products.filter(stock__gt=F('min_stock'))
        
    return render(request, 'products/product_list.html', {
        'products': products,
        'today': timezone.localdate()
    })

@login_required
def product_create(request):
    if request.method == 'POST':
        form = ProductForm(request.POST)
        if form.is_valid():
            form.save()
            return redirect('products:product_list')
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
            return redirect('products:product_list')
    else:
        form = ProductForm(instance=product)
    return render(request, 'products/product_form.html', {'form': form, 'title': 'Editar Producto'})

@login_required
def product_delete(request, pk):
    product = get_object_or_404(Product, pk=pk)
    if request.method == 'POST':
        product.delete()
        return redirect('products:product_list')
    return render(request, 'products/product_confirm_delete.html', {'product': product})

@login_required
def inventory_history(request):
    movements = InventoryMovement.objects.all()
    return render(request, 'products/inventory_history.html', {'movements': movements})

import pandas as pd
from django.http import HttpResponse
from django.contrib import messages
from .models import Product, Supplier

@login_required
def export_inventory_excel(request):
    products = Product.objects.select_related('supplier').all().values(
        'name', 'description', 'price', 'cost_price', 'stock', 'min_stock', 'expiry_date', 'supplier__name'
    )
    df = pd.DataFrame(list(products))
    
    # Renombrar columnas para que sean amigables
    df.columns = ['Nombre', 'Descripción', 'P. Venta', 'P. Costo', 'Stock', 'Min. Stock', 'Vencimiento', 'Proveedor']
    
    response = HttpResponse(content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    response['Content-Disposition'] = 'attachment; filename=Inventario_ImpulsoSmart.xlsx'
    
    with pd.ExcelWriter(response, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name='Inventario')
    
    return response

@login_required
def import_inventory_excel(request):
    if request.method == 'POST' and request.FILES.get('excel_file'):
        file = request.FILES['excel_file']
        try:
            df = pd.read_excel(file)
            count_created = 0
            count_updated = 0
            
            for _, row in df.iterrows():
                supplier_name = row.get('Proveedor')
                supplier = None
                if pd.notna(supplier_name):
                    supplier, _ = Supplier.objects.get_or_create(name=supplier_name)
                
                product, created = Product.objects.update_or_create(
                    name=row['Nombre'],
                    defaults={
                        'description': row.get('Descripción', ''),
                        'price': row['P. Venta'],
                        'cost_price': row.get('P. Costo', 0),
                        'stock': row['Stock'],
                        'min_stock': row.get('Min. Stock', 5),
                        'expiry_date': row.get('Vencimiento') if pd.notna(row.get('Vencimiento')) else None,
                        'supplier': supplier
                    }
                )
                if created: count_created += 1
                else: count_updated += 1
                
            messages.success(request, f"Éxito: {count_created} creados, {count_updated} actualizados.")
        except Exception as e:
            messages.error(request, f"Error al procesar el Excel: {str(e)}")
            
    return redirect('products:product_list')

@login_required
def stock_entry(request):
    if request.method == 'POST':
        form = InventoryMovementForm(request.POST)
        if form.is_valid():
            movement = form.save(commit=False)
            movement.user = request.user
            movement.movement_type = 'IN'
            
            # Actualizar Producto
            product = movement.product
            product.stock += movement.quantity
            # Sobreescribir precios con los nuevos valores del formulario
            product.cost_price = movement.cost_price
            product.price = movement.sale_price
            product.save()
            
            movement.save()
            messages.success(request, f"Se actualizaron {movement.quantity} uni. de {product.name} y sus precios.")
            return redirect('products:inventory_history')
    else:
        form = InventoryMovementForm()
    return render(request, 'products/stock_entry.html', {'form': form, 'title': 'Entrada de Stock'})

