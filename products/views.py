from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.db.models import Q, F, Sum, Count
from django.db import transaction
from django.utils import timezone
from django.http import HttpResponse, FileResponse
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib import messages
from django.db.models.functions import ExtractHour
from datetime import timedelta
import os
import json
import pandas as pd
from decimal import Decimal

from .models import Product, InventoryMovement, Category, StockLoss, Purchase, PurchaseDetail
from .forms import ProductForm, InventoryMovementForm
from sales.models import Sale, SaleDetail
from suppliers.models import Supplier
from pos_system.settings import BASE_DIR

@login_required
def product_list(request):
    query = request.GET.get('q')
    status_filter = request.GET.get('status')
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
    return render(request, 'products/product_list.html', {'products': products, 'today': timezone.localdate()})

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

@staff_member_required
def download_backup(request):
    db_path = os.path.join(BASE_DIR, 'db.sqlite3')
    if os.path.exists(db_path):
        timestamp = timezone.now().strftime('%Y-%m-%d_%H%M')
        response = FileResponse(open(db_path, 'rb'), content_type='application/x-sqlite3')
        response['Content-Disposition'] = f'attachment; filename=backup_impulso_smart_{timestamp}.sqlite3'
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
            products.update(price=F('price') * (1 + (percentage / 100)))
        else:
            Product.objects.all().update(price=F('price') * (1 + (percentage / 100)))
        messages.success(request, "Precios actualizados.")
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
            StockLoss.objects.create(product=product, quantity=qty, reason=reason, user=request.user)
            product.stock -= qty
            product.save()
            InventoryMovement.objects.create(product=product, quantity=qty, movement_type='OUT', reference=f"BAJA: {reason}", user=request.user)
            messages.success(request, "Baja registrada.")
        else:
            messages.error(request, "Stock insuficiente.")
    return redirect('products:product_list')

@staff_member_required
def business_intelligence(request):
    sales_by_hour = Sale.objects.annotate(hour=ExtractHour('date')).values('hour').annotate(count=Count('id'), total=Sum('total_amount')).order_by('hour')
    sixty_days_ago = timezone.now() - timedelta(days=60)
    sold_ids = SaleDetail.objects.filter(sale__date__gte=sixty_days_ago).values_list('product_id', flat=True)
    dead_products = Product.objects.exclude(id__in=sold_ids).order_by('name')
    supplier_ranking = InventoryMovement.objects.filter(movement_type='IN').values('product__supplier__name').annotate(total_spent=Sum(F('quantity') * F('cost_price'))).order_by('-total_spent')
    return render(request, 'products/bi_dashboard.html', {'sales_hour_data': list(sales_by_hour), 'dead_products': dead_products, 'supplier_ranking': supplier_ranking})

@login_required
def order_assistant(request):
    low_stock_products = Product.objects.filter(stock__lte=F('min_stock')).select_related('supplier')
    return render(request, 'products/order_assistant.html', {'low_stock_products': low_stock_products})

@login_required
def inventory_history(request):
    movements = InventoryMovement.objects.all()
    return render(request, 'products/inventory_history.html', {'movements': movements})

@login_required
def export_inventory_excel(request):
    products = Product.objects.select_related('supplier').all().values('name', 'description', 'price', 'cost_price', 'stock', 'min_stock', 'expiry_date', 'supplier__name')
    df = pd.DataFrame(list(products))
    response = HttpResponse(content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    response['Content-Disposition'] = 'attachment; filename=Inventario.xlsx'
    with pd.ExcelWriter(response, engine='openpyxl') as writer:
        df.to_excel(writer, index=False)
    return response

@login_required
def import_inventory_excel(request):
    if request.method == 'POST' and request.FILES.get('excel_file'):
        file = request.FILES['excel_file']
        try:
            df = pd.read_excel(file)
            for _, row in df.iterrows():
                supplier_name = row.get('Proveedor')
                supplier = None
                if pd.notna(supplier_name):
                    supplier, _ = Supplier.objects.get_or_create(name=supplier_name)
                Product.objects.update_or_create(name=row['Nombre'], defaults={'price': row['P. Venta'], 'stock': row['Stock'], 'supplier': supplier})
            messages.success(request, "Importación completada.")
        except Exception as e:
            messages.error(request, f"Error: {str(e)}")
    return redirect('products:product_list')

@login_required
def purchase_list(request):
    purchases = Purchase.objects.select_related('supplier', 'user').all()
    return render(request, 'products/purchase_list.html', {'purchases': purchases})

@login_required
def purchase_create(request):
    suppliers = Supplier.objects.all()
    products = Product.objects.all()
    if request.method == 'POST':
        supplier_id = request.POST.get('supplier_id')
        invoice_number = request.POST.get('invoice_number')
        items_data = request.POST.get('items_data')
        try:
            items = json.loads(items_data)
            with transaction.atomic():
                purchase = Purchase.objects.create(supplier_id=supplier_id, invoice_number=invoice_number, user=request.user)
                total = 0
                for item in items:
                    product = Product.objects.get(id=item['product_id'])
                    qty, cost = int(item['qty']), Decimal(item['cost'])
                    subtotal = qty * cost
                    total += subtotal
                    PurchaseDetail.objects.create(purchase=purchase, product=product, quantity=qty, cost_price=cost, subtotal=subtotal)
                    product.stock += qty
                    product.cost_price = cost
                    product.save()
                    InventoryMovement.objects.create(product=product, quantity=qty, cost_price=cost, movement_type='IN', reference=f"Compra #{purchase.id}", user=request.user)
                purchase.total_amount = total
                purchase.save()
                messages.success(request, "Compra registrada.")
                return redirect('products:purchase_list')
        except Exception as e:
            messages.error(request, f"Error: {str(e)}")
    return render(request, 'products/purchase_form.html', {'suppliers': suppliers, 'products': products})

@login_required
def stock_entry(request):
    if request.method == 'POST':
        form = InventoryMovementForm(request.POST)
        if form.is_valid():
            movement = form.save(commit=False)
            movement.user = request.user
            movement.movement_type = 'IN'
            product = movement.product
            product.stock += movement.quantity
            product.cost_price = movement.cost_price
            product.price = movement.sale_price
            product.save()
            movement.save()
            messages.success(request, "Stock actualizado.")
            return redirect('products:inventory_history')
    else:
        form = InventoryMovementForm()
    return render(request, 'products/stock_entry.html', {'form': form, 'title': 'Entrada de Stock'})
