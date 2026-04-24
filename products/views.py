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

from django.http import HttpResponse, FileResponse
from django.contrib.admin.views.decorators import staff_member_required
from .models import Category, StockLoss
from django.db.models import F
import os
from pos_system.settings import BASE_DIR

@staff_member_required
def download_backup(request):
    db_path = os.path.join(BASE_DIR, 'db.sqlite3')
    if os.path.exists(db_path):
        timestamp = timezone.now().strftime('%Y-%m-%d_%H%M')
        response = FileResponse(open(db_path, 'rb'), content_type='application/x-sqlite3')
        response['Content-Disposition'] = f'attachment; filename=backup_impulso_smart_{timestamp}.sqlite3'
        messages.success(request, "Copia de seguridad generada con éxito.")
        return response
    messages.error(request, "No se encontró el archivo de base de datos.")
    return redirect('products:admin_tools')

@staff_member_required
def bulk_price_update(request):
    if request.method == 'POST':
        category_id = request.POST.get('category')
        percentage = float(request.POST.get('percentage', 0))
        
        if category_id:
            products = Product.objects.filter(category_id=category_id)
            # Uso de F expressions para eficiencia a nivel de DB
            products.update(price=F('price') * (1 + (percentage / 100)))
            messages.success(request, f"Precios actualizados para {products.count()} productos (+{percentage}%).")
        else:
            # Si no hay categoría, actualizar todos
            Product.objects.all().update(price=F('price') * (1 + (percentage / 100)))
            messages.success(request, f"Todos los precios actualizados (+{percentage}%).")
            
        return redirect('products:admin_tools')
    
    categories = Category.objects.all()
    return render(request, 'products/admin_tools.html', {'categories': categories})

@login_required
def stock_loss_create(request):
    if request.method == 'POST':
        product_id = request.POST.get('product')
        qty = int(request.POST.get('quantity', 0))
        reason = request.POST.get('reason')
        
        product = get_object_or_404(Product, id=product_id)
        if product.stock >= qty:
            # Registrar Baja
            loss = StockLoss.objects.create(
                product=product,
                quantity=qty,
                reason=reason,
                user=request.user
            )
            # Actualizar Stock y Movimientos
            product.stock -= qty
            product.save()
            
            InventoryMovement.objects.create(
                product=product,
                quantity=qty,
                movement_type='OUT',
                reference=f"BAJA: {loss.get_reason_display()}",
                user=request.user
            )
            messages.success(request, f"Baja registrada: {qty} uni. de {product.name}.")
        else:
            messages.error(request, "Stock insuficiente para realizar la baja.")
            
    return redirect('products:product_list')

from django.db.models import Sum, Count, F
from django.db.models.functions import ExtractHour
from datetime import timedelta
from sales.models import Sale, SaleDetail

@staff_member_required
def business_intelligence(request):
    today = timezone.localdate()
    
    # 1. Horas Pico (Ventas por hora)
    sales_by_hour = Sale.objects.annotate(
        hour=ExtractHour('date')
    ).values('hour').annotate(
        count=Count('id'),
        total=Sum('total_amount')
    ).order_by('hour')
    
    # 2. Productos Muertos (Sin ventas en 60 días)
    sixty_days_ago = timezone.now() - timedelta(days=60)
    sold_ids = SaleDetail.objects.filter(sale__date__gte=sixty_days_ago).values_list('product_id', flat=True)
    dead_products = Product.objects.exclude(id__in=sold_ids).order_by('name')
    
    # 3. Ranking de Proveedores (Monto total comprado)
    supplier_ranking = InventoryMovement.objects.filter(
        movement_type='IN'
    ).values('product__supplier__name').annotate(
        total_spent=Sum(F('quantity') * F('cost_price')),
        qty_total=Sum('quantity')
    ).order_by('-total_spent')

    return render(request, 'products/bi_dashboard.html', {
        'sales_hour_data': list(sales_by_hour),
        'dead_products': dead_products,
        'supplier_ranking': supplier_ranking,
        'title': 'Inteligencia de Ventas'
    })

@login_required
def order_assistant(request):
    # Productos con stock bajo
    low_stock_products = Product.objects.filter(stock__lte=F('min_stock')).select_related('supplier')
    
    # Agrupar por proveedor para generar mensajes de WhatsApp
    context = {
        'low_stock_products': low_stock_products,
        'title': 'Asistente de Pedidos'
    }
    return render(request, 'products/order_assistant.html', context)

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

