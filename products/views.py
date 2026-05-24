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

from .models import Product, InventoryMovement, Category, StockLoss, Purchase, PurchaseDetail, PurchaseOrder, PurchaseOrderDetail
from .forms import ProductForm, InventoryMovementForm, CategoryForm
from sales.models import Sale, SaleDetail, SaleReturn, SaleReturnDetail
from suppliers.models import Supplier
from pos_system.settings import BASE_DIR

@staff_member_required
def category_list(request):
    categories = Category.objects.all().annotate(product_count=Count('products'))
    return render(request, 'products/category_list.html', {'categories': categories})

@staff_member_required
def category_create(request):
    if request.method == 'POST':
        form = CategoryForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, "Categoría creada exitosamente.")
            return redirect('products:category_list')
    else:
        form = CategoryForm()
    return render(request, 'products/category_form.html', {'form': form, 'title': 'Nueva Categoría'})

@staff_member_required
def category_update(request, pk):
    category = get_object_or_404(Category, pk=pk)
    if request.method == 'POST':
        form = CategoryForm(request.POST, instance=category)
        if form.is_valid():
            form.save()
            messages.success(request, "Categoría actualizada.")
            return redirect('products:category_list')
    else:
        form = CategoryForm(instance=category)
    return render(request, 'products/category_form.html', {'form': form, 'title': 'Editar Categoría'})

from core.models import ActivityLog

@staff_member_required
def category_delete(request, pk):
    category = get_object_or_404(Category, pk=pk)
    if request.method == 'POST':
        ActivityLog.objects.create(
            user=request.user,
            action=f"Categoría eliminada: {category.name}",
            module="Productos"
        )
        category.delete()
        messages.success(request, "Categoría eliminada.")
        return redirect('products:category_list')
    return render(request, 'products/category_confirm_delete.html', {'category': category})

@login_required
def product_list(request):
    query = request.GET.get('q')
    status_filter = request.GET.get('status')
    category_id = request.GET.get('category')
    
    products = Product.objects.select_related('supplier', 'category').all()
    categories = Category.objects.all()

    if query:
        products = products.filter(
            Q(name__icontains=query) | 
            Q(description__icontains=query) |
            Q(sku__icontains=query)
        )
    
    if category_id:
        products = products.filter(category_id=category_id)

    if status_filter == 'danger':
        products = products.filter(stock__lte=0)
    elif status_filter == 'warning':
        products = products.filter(stock__gt=0, stock__lte=F('min_stock'))
    elif status_filter == 'success':
        products = products.filter(stock__gt=F('min_stock'))
        
    return render(request, 'products/product_list.html', {
        'products': products, 
        'categories': categories,
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

@staff_member_required
def product_delete(request, pk):
    product = get_object_or_404(Product, pk=pk)
    if request.method == 'POST':
        ActivityLog.objects.create(
            user=request.user,
            action=f"Producto eliminado: {product.name} (SKU: {product.sku})",
            module="Productos"
        )
        product.delete()
        messages.success(request, f"Producto '{product.name}' eliminado exitosamente.")
        return redirect('products:product_list')
    return render(request, 'products/product_confirm_delete.html', {'product': product})

@staff_member_required
def download_backup(request):
    if not request.user.is_superuser:
        messages.error(request, "Solo el administrador del sistema puede descargar backups.")
        return redirect('dashboard')
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
    if not request.user.is_superuser and not request.user.groups.filter(name='Administradores').exists():
        messages.error(request, "Solo administradores pueden actualizar precios masivamente.")
        return redirect('products:admin_tools')
    if request.method == 'POST':
        category_id = request.POST.get('category')
        percentage = float(request.POST.get('percentage', 0))
        
        target = "Todos los productos"
        if category_id:
            category = Category.objects.get(id=category_id)
            target = f"Categoría: {category.name}"
            products = Product.objects.filter(category_id=category_id)
            products.update(price=F('price') * (1 + (percentage / 100)))
        else:
            Product.objects.all().update(price=F('price') * (1 + (percentage / 100)))
        
        ActivityLog.objects.create(
            user=request.user,
            action=f"Actualización masiva de precios ({percentage}%)",
            module="Productos",
            details=f"Target: {target}"
        )
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
    if not request.user.is_superuser and not request.user.groups.filter(name='Administradores').exists():
        messages.error(request, "No tenés permiso para acceder a Inteligencia de Ventas.")
        return redirect('dashboard')

    period = request.GET.get('period', 'month')
    start_date = None
    end_date = timezone.now()

    if period == 'today':
        start_date = timezone.now().replace(hour=0, minute=0, second=0, microsecond=0)
    elif period == 'week':
        start_date = timezone.now() - timedelta(days=7)
    elif period == 'month':
        start_date = timezone.now().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    elif period == 'all':
        start_date = None

    # Filtros base
    sale_filters = Q()
    expense_filters = Q()
    return_filters = Q()
    movement_filters = Q(movement_type='IN')

    if start_date:
        sale_filters &= Q(date__gte=start_date)
        expense_filters &= Q(date__gte=start_date)
        return_filters &= Q(date__gte=start_date)
        movement_filters &= Q(product__inventorymovement__date__gte=start_date) # Ajuste para ranking de proveedores

    # 1. Gráfico de Horas Pico
    sales_by_hour = Sale.objects.filter(sale_filters).annotate(hour=ExtractHour('date')).values('hour').annotate(count=Count('id'), total=Sum('total_amount')).order_by('hour')

    # 2. Top Productos (Más vendidos)
    top_products = SaleDetail.objects.filter(sale__date__gte=start_date if start_date else timezone.make_aware(timezone.datetime(2000,1,1))).values('product__name').annotate(total_qty=Sum('quantity')).order_by('-total_qty')[:10]

    # 3. Reporte de Productos Muertos (>60 días sin ventas)
    # Excluimos productos creados en los últimos 30 días para no penalizar stock nuevo
    thirty_days_ago = timezone.now() - timedelta(days=30)
    sixty_days_ago = timezone.now() - timedelta(days=60)
    
    sold_ids = SaleDetail.objects.filter(sale__date__gte=sixty_days_ago).values_list('product_id', flat=True)
    dead_products = Product.objects.exclude(id__in=sold_ids).filter(created_at__lt=thirty_days_ago, stock__gt=0).annotate(capital=F('stock') * F('cost_price')).order_by('-capital')

    total_dead_capital = dead_products.aggregate(total=Sum(F('stock') * F('cost_price')))['total'] or 0

    # 4. Ranking de Proveedores (Por inversión en compras)
    # Ajuste: Filtramos directamente por el campo 'date' del modelo InventoryMovement
    movement_filters = Q(movement_type='IN')
    if start_date:
        movement_filters &= Q(date__gte=start_date)
    
    supplier_ranking = InventoryMovement.objects.filter(movement_filters).values('product__supplier__name').annotate(total_spent=Sum(F('quantity') * F('cost_price'))).order_by('-total_spent')

    # 5. Ganancia Neta (Ventas - Costos - Gastos - Devoluciones)
    from finance.models import CashExpense

    total_revenue = Sale.objects.filter(sale_filters).aggregate(total=Sum('total_amount'))['total'] or 0
    total_cost = SaleDetail.objects.filter(sale__date__gte=start_date if start_date else timezone.make_aware(timezone.datetime(2000,1,1))).aggregate(total=Sum(F('quantity') * F('cost_price_at_sale')))['total'] or 0
    total_expenses = CashExpense.objects.filter(expense_filters).aggregate(total=Sum('amount'))['total'] or 0
    total_returns = SaleReturn.objects.filter(return_filters).aggregate(total=Sum('total_amount'))['total'] or 0
    
    # 6. Impacto de Marketing
    total_promo_discounts = Sale.objects.filter(sale_filters).aggregate(total=Sum('promo_discount'))['total'] or 0
    total_points_discounts = Sale.objects.filter(sale_filters).aggregate(total=Sum('points_discount'))['total'] or 0
    total_marketing_investment = total_promo_discounts + total_points_discounts

    net_profit = total_revenue - total_cost - total_expenses - total_returns

    context = {
        'period': period,
        'sales_hour_data': list(sales_by_hour), 
        'top_products': list(top_products),
        'dead_products': dead_products[:15],
        'total_dead_capital': total_dead_capital,
        'supplier_ranking': supplier_ranking,
        'net_profit_data': {
            'revenue': total_revenue,
            'cost': total_cost,
            'expenses': total_expenses,
            'returns': total_returns,
            'profit': net_profit
        },
        'marketing_impact': {
            'promo_discounts': total_promo_discounts,
            'points_discounts': total_points_discounts,
            'total_investment': total_marketing_investment
        }
    }
    return render(request, 'products/bi_dashboard.html', context)

@login_required
def export_advanced_excel(request):
    """Genera un Excel complejo con múltiples hojas: Ventas Mensuales y Stock Crítico"""
    today = timezone.localdate()
    first_day_month = today.replace(day=1)

    # Hoja 1: Ventas del Mes
    sales = Sale.objects.filter(date__date__gte=first_day_month).values('id', 'date', 'customer__full_name', 'total_amount', 'payment_method')
    df_sales = pd.DataFrame(list(sales))
    if not df_sales.empty:
        df_sales['date'] = df_sales['date'].dt.strftime('%d/%m/%Y %H:%M')

    # Hoja 2: Stock Crítico
    critical_stock = Product.objects.filter(stock__lte=F('min_stock')).values('name', 'stock', 'min_stock', 'supplier__name')
    df_critical = pd.DataFrame(list(critical_stock))

    response = HttpResponse(content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    response['Content-Disposition'] = f'attachment; filename=Reporte_Avanzado_{today.strftime("%m_%Y")}.xlsx'

    with pd.ExcelWriter(response, engine='openpyxl') as writer:
        if not df_sales.empty:
            df_sales.to_excel(writer, sheet_name='Ventas del Mes', index=False)
        if not df_critical.empty:
            df_critical.to_excel(writer, sheet_name='Stock Crítico', index=False)

    return response

@login_required
def order_assistant(request):
    low_stock_products = Product.objects.filter(stock__lte=F('min_stock')).select_related('supplier')
    
    if request.method == 'POST':
        # Agrupar productos por proveedor para generar órdenes automáticas
        suppliers_low_stock = low_stock_products.values_list('supplier_id', flat=True).distinct()
        created_orders = 0
        for s_id in suppliers_low_stock:
            if not s_id: continue
            prods = low_stock_products.filter(supplier_id=s_id)
            with transaction.atomic():
                order = PurchaseOrder.objects.create(supplier_id=s_id)
                total = 0
                for p in prods:
                    qty_to_order = (p.min_stock * 2) - p.stock # Sugerencia: reponer hasta el doble del mínimo
                    if qty_to_order <= 0: qty_to_order = p.min_stock
                    PurchaseOrderDetail.objects.create(
                        order=order,
                        product=p,
                        quantity=qty_to_order,
                        estimated_cost=p.cost_price
                    )
                    total += qty_to_order * p.cost_price
                order.total_amount = total
                order.save()
                created_orders += 1
        
        if created_orders > 0:
            messages.success(request, f"Se han generado {created_orders} órdenes de compra automáticas.")
        else:
            messages.info(request, "No se generaron órdenes. Verifique que los productos tengan proveedores asignados.")
        return redirect('products:purchase_order_list')

    return render(request, 'products/order_assistant.html', {'low_stock_products': low_stock_products})

from .utils import generate_product_labels
from io import BytesIO

@staff_member_required
def export_labels(request):
    product_ids = request.GET.getlist('products')
    if not product_ids:
        messages.error(request, "Debe seleccionar al menos un producto.")
        return redirect('products:product_list')
    
    products = Product.objects.filter(id__in=product_ids)
    buffer = BytesIO()
    generate_product_labels(buffer, products)
    buffer.seek(0)
    
    return FileResponse(buffer, as_attachment=False, filename="Etiquetas_Productos.pdf")

@staff_member_required
def update_purchase_order_status(request, pk):
    order = get_object_or_404(PurchaseOrder, pk=pk)
    new_status = request.POST.get('status')
    
    if new_status == 'RECEIVED' and order.status != 'RECEIVED':
        with transaction.atomic():
            for detail in order.details.all():
                product = detail.product
                product.stock += detail.quantity
                product.cost_price = detail.estimated_cost
                product.save()
                
                InventoryMovement.objects.create(
                    product=product,
                    quantity=detail.quantity,
                    movement_type='IN',
                    reference=f"Orden Compra #{order.id}",
                    user=request.user
                )
            order.status = 'RECEIVED'
            order.save()
            messages.success(request, f"Orden #{order.id} recibida. Stock actualizado.")
            
    elif new_status == 'PAID' and order.status == 'RECEIVED':
        # Impactar en caja (egreso)
        from finance.models import CashSession, CashExpense
        session = CashSession.objects.filter(is_open=True, user=request.user).first()
        if not session:
            messages.error(request, "Debes tener una caja abierta para marcar como PAGADA e impactar el egreso.")
            return redirect('products:purchase_order_list')
            
        with transaction.atomic():
            CashExpense.objects.create(
                session=session,
                amount=order.total_amount,
                description=f"Pago OC #{order.id} - {order.supplier.name}"
            )
            order.status = 'PAID'
            order.save()
            messages.success(request, f"Orden #{order.id} pagada e impactada en caja.")
    else:
        order.status = new_status
        order.save()
        messages.success(request, f"Estado de Orden #{order.id} actualizado a {order.get_status_display()}.")
        
    return redirect('products:purchase_order_list')

@staff_member_required
def purchase_order_list(request):
    orders = PurchaseOrder.objects.select_related('supplier').all()
    return render(request, 'products/purchase_order_list.html', {'orders': orders})

@staff_member_required
def supplier_ranking(request):
    # 1. Ranking por Volumen de Compra
    volume_ranking = Purchase.objects.values('supplier__name').annotate(
        total_spent=Sum('total_amount'),
        purchase_count=Count('id')
    ).order_by('-total_spent')

    # 2. Ranking por Margen de Ganancia (Basado en productos vendidos de cada proveedor)
    # Margen = (Precio Venta - Precio Costo) / Precio Venta
    margin_ranking = SaleDetail.objects.filter(product__supplier__isnull=False).values('product__supplier__name').annotate(
        total_revenue=Sum('subtotal'),
        total_cost=Sum(F('quantity') * F('cost_price_at_sale')),
    ).annotate(
        absolute_margin=F('total_revenue') - F('total_cost')
    ).order_by('-absolute_margin')

    # Calculamos el porcentaje de margen en Python para evitar divisiones por cero complejas en SQL
    ranking_data = []
    for item in margin_ranking:
        revenue = item['total_revenue']
        cost = item['total_cost']
        margin_pct = ((revenue - cost) / revenue * 100) if revenue > 0 else 0
        ranking_data.append({
            'supplier': item['product__supplier__name'],
            'revenue': revenue,
            'margin_abs': item['absolute_margin'],
            'margin_pct': margin_pct
        })

    return render(request, 'products/supplier_ranking.html', {
        'volume_ranking': volume_ranking,
        'ranking_data': ranking_data
    })

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
            
            # Mapeo de nombres de columnas (soporta tanto nombres de exportación como amigables)
            column_mapping = {
                'Nombre': 'name', 'name': 'name',
                'SKU': 'sku', 'sku': 'sku',
                'P. Venta': 'price', 'price': 'price',
                'P. Costo': 'cost_price', 'cost_price': 'cost_price',
                'Stock': 'stock', 'stock': 'stock',
                'Stock Minimo': 'min_stock', 'min_stock': 'min_stock',
                'Proveedor': 'supplier', 'supplier__name': 'supplier',
                'Categoria': 'category', 'category__name': 'category',
                'Descripcion': 'description', 'description': 'description'
            }
            
            # Normalizar columnas del DataFrame
            df.columns = [column_mapping.get(c, c) for c in df.columns]
            
            imported_count = 0
            for _, row in df.iterrows():
                # Buscar o crear proveedor
                supplier = None
                if 'supplier' in row and pd.notna(row['supplier']):
                    supplier, _ = Supplier.objects.get_or_create(name=str(row['supplier']))
                
                # Buscar o crear categoría
                category = None
                if 'category' in row and pd.notna(row['category']):
                    category, _ = Category.objects.get_or_create(name=str(row['category']))

                # Preparar datos
                defaults = {}
                if 'price' in row: defaults['price'] = Decimal(str(row['price']))
                if 'cost_price' in row: defaults['cost_price'] = Decimal(str(row['cost_price']))
                if 'stock' in row: defaults['stock'] = int(row['stock'])
                if 'min_stock' in row: defaults['min_stock'] = int(row['min_stock'])
                if 'description' in row: defaults['description'] = str(row['description'])
                if supplier: defaults['supplier'] = supplier
                if category: defaults['category'] = category

                # Usar SKU como llave principal si existe, si no, el nombre
                if 'sku' in row and pd.notna(row['sku']):
                    Product.objects.update_or_create(sku=str(row['sku']), defaults={'name': row['name'], **defaults})
                else:
                    Product.objects.update_or_create(name=row['name'], defaults=defaults)
                
                imported_count += 1
                
            messages.success(request, f"Importación completada: {imported_count} productos procesados.")
        except Exception as e:
            messages.error(request, f"Error al importar: {str(e)}")
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
def stock_entry_scanner(request):
    if request.method == 'POST':
        barcode = request.POST.get('barcode')
        qty = int(request.POST.get('quantity', 1))
        
        product = Product.objects.filter(Q(barcode=barcode) | Q(sku=barcode)).first()
        
        if product:
            with transaction.atomic():
                product.stock += qty
                product.save()
                
                InventoryMovement.objects.create(
                    product=product,
                    quantity=qty,
                    movement_type='IN',
                    reference="Ingreso por Escáner",
                    user=request.user
                )
            messages.success(request, f"Se sumaron {qty} unidades a {product.name}. Nuevo stock: {product.stock}")
        else:
            messages.error(request, f"Producto no encontrado: {barcode}")
            
        return redirect('products:stock_entry_scanner')
        
    return render(request, 'products/stock_entry_scanner.html')
