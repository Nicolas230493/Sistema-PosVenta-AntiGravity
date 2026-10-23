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
from decimal import Decimal, InvalidOperation

import csv
from .models import Product, InventoryMovement, Category, StockLoss, Purchase, PurchaseDetail, PurchaseOrder, PurchaseOrderDetail, LOSS_REASONS
from .forms import ProductForm, InventoryMovementForm, CategoryForm, StockAdjustmentForm
from .importers import import_products, parse_decimal
from .services import DEFAULT_COVERAGE_DAYS, purchase_suggestions
from .utils import generate_product_labels, generate_inventory_valuation_pdf
from sales.models import Sale, SaleDetail, SaleReturn, SaleReturnDetail
from suppliers.models import Supplier, SupplierAccountMovement
from suppliers.services import SupplierError, receive_purchase_order, receive_stock, register_supplier_payment
from django.utils.dateparse import parse_date
from django.views.decorators.http import require_POST
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
        form = ProductForm(request.POST, user=request.user)
        if form.is_valid():
            form.save()
            messages.success(request, "Producto creado exitosamente.")
            return redirect('products:product_list')
    else:
        form = ProductForm(user=request.user)
    return render(request, 'products/product_form.html', {'form': form, 'title': 'Nuevo Producto'})

@login_required
def product_update(request, pk):
    product = get_object_or_404(Product, pk=pk)
    if request.method == 'POST':
        form = ProductForm(request.POST, instance=product, user=request.user)
        if form.is_valid():
            form.save()
            messages.success(request, "Producto actualizado.")
            return redirect('products:product_list')
    else:
        form = ProductForm(instance=product, user=request.user)
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
        if percentage <= -100:
            messages.error(request, "El porcentaje no puede dejar precios en cero o negativos.")
            return redirect('products:admin_tools')
        
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
        try:
            product_id = request.POST.get('product')
            qty = Decimal(str(request.POST.get('quantity', 0)))
            reason = request.POST.get('reason')
            if qty <= 0:
                raise ValueError("La cantidad debe ser mayor a cero.")
            with transaction.atomic():
                product = get_object_or_404(Product.objects.select_for_update(), id=product_id)
                if product.is_discrete and (qty % Decimal('1')) != Decimal('0'):
                    raise ValueError("Este producto es de unidad entera y no admite cantidades fraccionales.")
                if product.stock >= qty:
                    reason_display = dict(LOSS_REASONS).get(reason, reason)
                    StockLoss.objects.create(product=product, quantity=qty, unit_cost=product.cost_price, reason=reason, user=request.user)
                    product.stock -= qty
                    product.save()
                    InventoryMovement.objects.create(
                        product=product,
                        quantity=qty,
                        movement_type='OUT',
                        reference=f"BAJA: {reason_display}",
                        reason=f"Baja por {reason_display}",
                        user=request.user
                    )
                    messages.success(request, f"Baja registrada para {product.name}.")
                else:
                    messages.error(request, "Stock insuficiente.")
        except (ValueError, InvalidOperation) as e:
            messages.error(request, str(e))
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
        sale_filters &= Q(fecha_hora__gte=start_date)
        expense_filters &= Q(date__gte=start_date)
        return_filters &= Q(date__gte=start_date)
        movement_filters &= Q(product__inventorymovement__date__gte=start_date) # Ajuste para ranking de proveedores

    # 1. Gráfico de Horas Pico
    sales_by_hour = Sale.objects.filter(sale_filters).annotate(hour=ExtractHour('fecha_hora')).values('hour').annotate(count=Count('id'), total=Sum('total_amount')).order_by('hour')

    # 2. Top Productos (Más vendidos)
    top_products = SaleDetail.objects.filter(sale__fecha_hora__gte=start_date if start_date else timezone.make_aware(timezone.datetime(2000,1,1))).values('product__name').annotate(total_qty=Sum('quantity')).order_by('-total_qty')[:10]

    # 3. Reporte de Productos Muertos (>60 días sin ventas)
    # Excluimos productos creados en los últimos 30 días para no penalizar stock nuevo
    thirty_days_ago = timezone.now() - timedelta(days=30)
    sixty_days_ago = timezone.now() - timedelta(days=60)
    
    sold_ids = SaleDetail.objects.filter(sale__fecha_hora__gte=sixty_days_ago).values_list('product_id', flat=True)
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
    total_cost = SaleDetail.objects.filter(sale__fecha_hora__gte=start_date if start_date else timezone.make_aware(timezone.datetime(2000,1,1))).aggregate(total=Sum(F('quantity') * F('unit_cost')))['total'] or 0
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
    sales = Sale.objects.filter(fecha_hora__date__gte=first_day_month).values('id', 'fecha_hora', 'customer__full_name', 'total_amount', 'payment_method__name')
    df_sales = pd.DataFrame(list(sales))
    if not df_sales.empty:
        df_sales['fecha_hora'] = df_sales['fecha_hora'].dt.strftime('%d/%m/%Y %H:%M')

    # Hoja 2: Stock Crítico
    critical_stock = Product.objects.filter(stock__lte=F('min_stock')).values('name', 'stock', 'min_stock', 'supplier__name')
    df_critical = pd.DataFrame(list(critical_stock))

    response = HttpResponse(content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    response['Content-Disposition'] = f'attachment; filename=Reporte_Avanzado_{today.strftime("%m_%Y")}.xlsx'

    with pd.ExcelWriter(response, engine='openpyxl') as writer:
        wrote_sheet = False
        if not df_sales.empty:
            df_sales.to_excel(writer, sheet_name='Ventas del Mes', index=False)
            wrote_sheet = True
        if not df_critical.empty:
            df_critical.to_excel(writer, sheet_name='Stock Crítico', index=False)
            wrote_sheet = True
        if not wrote_sheet:
            pd.DataFrame([{'Estado': 'Sin datos para el período'}]).to_excel(writer, sheet_name='Resumen', index=False)

    return response

def _coverage_days(value):
    try:
        days = int(value)
    except (TypeError, ValueError):
        return DEFAULT_COVERAGE_DAYS
    return min(max(days, 1), 90)


@login_required
def order_assistant(request):
    """SPEC-INV-09 §9.1: sugerencia de compra por proveedor según la velocidad de venta de 14 días."""
    coverage_days = _coverage_days(request.POST.get('dias') or request.GET.get('dias'))
    groups = purchase_suggestions(coverage_days)

    if request.method == 'POST':
        created_orders = 0
        for group in groups:
            if not group['supplier']:
                continue
            with transaction.atomic():
                order = PurchaseOrder.objects.create(
                    supplier=group['supplier'],
                    notes=f"Sugerencia automática: cobertura {coverage_days} días (V14)",
                )
                total = Decimal('0.00')
                for item in group['items']:
                    product = item['product']
                    PurchaseOrderDetail.objects.create(
                        order=order,
                        product=product,
                        quantity=item['suggested'],
                        estimated_cost=product.cost_price,
                    )
                    total += item['suggested'] * product.cost_price
                order.total_amount = total
                order.save(update_fields=['total_amount'])
                created_orders += 1

        if created_orders > 0:
            messages.success(request, f"Se han generado {created_orders} órdenes de compra automáticas.")
        else:
            messages.info(request, "No se generaron órdenes. Verifique que los productos sugeridos tengan proveedores asignados.")
        return redirect('products:purchase_order_list')

    lines = [f"- {item['product'].name}: {item['suggested']} {item['product'].unit}" for g in groups for item in g['items']]
    whatsapp_text = "Pedido de mercadería:\n" + "\n".join(lines) if lines else ""
    return render(request, 'products/order_assistant.html', {
        'groups': groups,
        'coverage_days': coverage_days,
        'whatsapp_text': whatsapp_text,
    })

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
@require_POST
def update_purchase_order_status(request, pk):
    order = get_object_or_404(PurchaseOrder, pk=pk)
    new_status = request.POST.get('status')

    try:
        if new_status == 'RECEIVED':
            # SPEC-SUP-07: CPP + stock + PriceLog + PURCHASE_IN + deuda con el proveedor
            due_date = parse_date(request.POST.get('due_date') or '')
            receive_purchase_order(order=order, user=request.user, due_date=due_date)
            messages.success(request, f"Orden #{order.id} recibida. Stock y costo promedio actualizados; deuda registrada en la cuenta del proveedor.")
        elif new_status == 'PAID':
            if order.status != 'RECEIVED':
                raise SupplierError("Solo se pueden pagar órdenes recibidas.")
            source = request.POST.get('source') or SupplierAccountMovement.SOURCE_CASH
            with transaction.atomic():
                register_supplier_payment(
                    supplier=order.supplier, amount=order.total_amount, source=source,
                    user=request.user, purchase_order=order,
                )
                order.status = 'PAID'
                order.save(update_fields=['status'])
            origin = "impactada en caja" if source == SupplierAccountMovement.SOURCE_CASH else "pagada por banco"
            messages.success(request, f"Orden #{order.id} {origin}.")
        elif new_status in ('PENDING', 'ORDERED', 'CANCELLED'):
            if order.status in ('RECEIVED', 'PAID'):
                raise SupplierError("Una orden recibida no puede volver atrás ni cancelarse: registre un ajuste de stock.")
            order.status = new_status
            order.save(update_fields=['status'])
            messages.success(request, f"Estado de Orden #{order.id} actualizado a {order.get_status_display()}.")
        else:
            raise SupplierError("Estado inválido.")
    except SupplierError as e:
        messages.error(request, str(e))

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
        total_cost=Sum(F('quantity') * F('unit_cost')),
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
    movements = InventoryMovement.objects.select_related('product', 'user').all()
    return render(request, 'products/inventory_history.html', {'movements': movements})

def _filter_inventory_queryset(request):
    status_filter = request.GET.get('status')
    low_stock = request.GET.get('low_stock')
    category_id = request.GET.get('category')
    q = request.GET.get('q')

    qs = Product.objects.select_related('supplier', 'category').all().order_by('name')

    if q:
        qs = qs.filter(Q(name__icontains=q) | Q(sku__icontains=q) | Q(barcode__icontains=q))

    if category_id:
        qs = qs.filter(category_id=category_id)

    if low_stock in ('1', 'true', 'True') or status_filter in ('low', 'warning', 'danger'):
        qs = qs.filter(stock__lte=F('min_stock'))
    elif status_filter == 'success':
        qs = qs.filter(stock__gt=F('min_stock'))

    return qs

@login_required
def export_inventory_excel(request):
    """SPEC-AUD-STOCK-06 §6.3.10: Exportación de inventario Excel (completo o filtrado por stock bajo)."""
    qs = _filter_inventory_queryset(request)
    products = qs.values(
        'sku', 'barcode', 'name', 'category__name', 'description',
        'unit', 'price', 'cost_price', 'stock', 'min_stock', 'expiry_date', 'supplier__name'
    )
    df = pd.DataFrame(list(products))
    if not df.empty:
        df['Valuacion Costo'] = df['stock'] * df['cost_price']
        df['Valuacion Venta'] = df['stock'] * df['price']

    response = HttpResponse(content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    is_low = request.GET.get('low_stock') in ('1', 'true', 'True') or request.GET.get('status') in ('low', 'warning', 'danger')
    filename = "Inventario_Stock_Bajo.xlsx" if is_low else "Inventario_Completo.xlsx"
    response['Content-Disposition'] = f'attachment; filename={filename}'
    with pd.ExcelWriter(response, engine='openpyxl') as writer:
        df.to_excel(writer, index=False)
    return response

@login_required
def export_inventory_csv(request):
    """SPEC-AUD-STOCK-06 §6.3.10: Exportación de inventario CSV (completo o filtrado por stock bajo)."""
    products = _filter_inventory_queryset(request)
    is_low = request.GET.get('low_stock') in ('1', 'true', 'True') or request.GET.get('status') in ('low', 'warning', 'danger')
    filename = "Inventario_Stock_Bajo.csv" if is_low else "Inventario_Completo.csv"

    response = HttpResponse(content_type='text/csv; charset=utf-8-sig')
    response['Content-Disposition'] = f'attachment; filename="{filename}"'

    writer = csv.writer(response)
    writer.writerow([
        'SKU', 'Código de Barras', 'Producto', 'Categoría', 'Proveedor',
        'Unidad', 'Stock Actual', 'Stock Mínimo', 'Precio Costo ($)', 'Precio Venta ($)',
        'Valuación Costo ($)', 'Valuación Venta ($)'
    ])

    for p in products:
        val_cost = (p.stock * p.cost_price).quantize(Decimal('0.01'))
        val_sale = (p.stock * p.price).quantize(Decimal('0.01'))
        writer.writerow([
            p.sku or '',
            p.barcode or '',
            p.name,
            p.category.name if p.category else 'Sin Categoría',
            p.supplier.name if p.supplier else 'Sin Proveedor',
            p.get_unit_display(),
            p.stock,
            p.min_stock,
            p.cost_price,
            p.price,
            val_cost,
            val_sale
        ])

    return response

@login_required
def export_inventory_pdf(request):
    """SPEC-AUD-STOCK-06 §6.3.10: Exportación de inventario en PDF valorizado."""
    products = list(_filter_inventory_queryset(request))
    is_low = request.GET.get('low_stock') in ('1', 'true', 'True') or request.GET.get('status') in ('low', 'warning', 'danger')
    title = "Inventario Valorizado — Stock Crítico / Por Reponer" if is_low else "Inventario Valorizado — Catálogo Completo"

    buffer = BytesIO()
    generate_inventory_valuation_pdf(buffer, products, report_title=title)
    buffer.seek(0)

    filename = "Inventario_Valorizado_Stock_Bajo.pdf" if is_low else "Inventario_Valorizado_Completo.pdf"
    return FileResponse(buffer, as_attachment=True, filename=filename)

@login_required
def product_kardex(request, pk):
    """SPEC-AUD-STOCK-06 §6.3.9: Kardex cronológico por producto con saldo histórico."""
    product = get_object_or_404(Product, pk=pk)
    chronological_movements = list(
        InventoryMovement.objects.filter(product=product).select_related('user').order_by('date', 'id')
    )

    kardex_rows = []
    running_balance = Decimal('0.000')
    for m in chronological_movements:
        if m.movement_type in ('IN', 'PURCHASE_IN', 'SALE_CANCEL_IN', 'AUTO_ADJUST_IN'):
            running_balance += m.quantity
        elif m.movement_type in ('OUT',):
            running_balance -= m.quantity
        elif m.movement_type == 'ADJ':
            if m.reference and m.reference.startswith('Ajuste Manual (-'):
                running_balance -= m.quantity
            else:
                running_balance += m.quantity

        kardex_rows.append({
            'movement': m,
            'running_balance': running_balance,
        })

    kardex_rows.reverse()

    return render(request, 'products/product_kardex.html', {
        'product': product,
        'kardex_rows': kardex_rows,
        'current_stock': product.stock,
    })

@login_required
def stock_adjust(request):
    """SPEC-AUD-STOCK-06 §6.2.6: Ajustes manuales auditables con campo reason obligatorio."""
    if request.method == 'POST':
        form = StockAdjustmentForm(request.POST)
        if form.is_valid():
            product = form.cleaned_data['product']
            adj_type = form.cleaned_data['adjustment_type']
            qty = form.cleaned_data['quantity']
            reason = form.cleaned_data['reason'].strip()

            with transaction.atomic():
                prod = Product.objects.select_for_update().get(pk=product.pk)
                old_stock = prod.stock
                if adj_type == 'SET':
                    diff = qty - old_stock
                    prod.stock = qty
                    mov_qty = abs(diff)
                    ref = f"Conteo Físico ({old_stock} -> {qty})"
                elif adj_type == 'ADD':
                    prod.stock = old_stock + qty
                    mov_qty = qty
                    ref = f"Ajuste Positivo (+{qty})"
                elif adj_type == 'SUB':
                    prod.stock = max(Decimal('0.000'), old_stock - qty)
                    mov_qty = qty
                    ref = f"Ajuste Negativo (-{qty})"

                prod.save()
                InventoryMovement.objects.create(
                    product=prod,
                    quantity=mov_qty,
                    movement_type='ADJ',
                    reference=ref,
                    reason=reason,
                    user=request.user
                )

            messages.success(request, f"Ajuste de stock registrado para '{prod.name}'. Nuevo stock: {prod.stock}")
            return redirect('products:product_kardex', pk=prod.pk)
    else:
        initial = {}
        product_id = request.GET.get('product')
        if product_id:
            initial['product'] = product_id
        form = StockAdjustmentForm(initial=initial)

    return render(request, 'products/stock_adjust.html', {'form': form})

MAX_IMPORT_WARNINGS = 20

@login_required
def import_inventory_excel(request):
    """Importa productos desde CSV o Excel sin abortar nunca la petición (SPEC-IMP-04)."""
    uploaded = request.FILES.get('excel_file') or request.FILES.get('file')
    if request.method == 'POST' and uploaded:
        try:
            created, updated, warnings = import_products(uploaded)
        except Exception as e:
            messages.error(request, f"No se pudo leer el archivo: {e}")
            return redirect('products:product_list')

        for warning in warnings[:MAX_IMPORT_WARNINGS]:
            messages.warning(request, warning)
        if len(warnings) > MAX_IMPORT_WARNINGS:
            messages.warning(request, f"... y {len(warnings) - MAX_IMPORT_WARNINGS} filas omitidas más.")
        messages.success(request, f"Importación completada: {created} productos creados, {updated} actualizados, {len(warnings)} filas omitidas.")
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
                    product = Product.objects.select_for_update().get(id=item['product_id'])
                    qty, cost = Decimal(str(item['qty'])), Decimal(str(item['cost']))
                    if qty <= 0 or cost < 0:
                        raise Exception("Las cantidades deben ser mayores a cero y los costos no pueden ser negativos.")
                    subtotal = (qty * cost).quantize(Decimal('0.01'))
                    total += subtotal
                    PurchaseDetail.objects.create(purchase=purchase, product=product, quantity=int(qty) if product.is_discrete else qty, cost_price=cost, subtotal=subtotal)
                    receive_stock(
                        product=product,
                        quantity=qty,
                        unit_cost=cost,
                        reference=f"Compra #{purchase.id}",
                        reason=f"Compra Factura {purchase.invoice_number or purchase.id}",
                        user=request.user,
                    )
                purchase.total_amount = total
                purchase.save()
                messages.success(request, "Compra registrada exitosamente.")
                return redirect('products:purchase_list')
        except Exception as e:
            messages.error(request, f"Error: {str(e)}")
    return render(request, 'products/purchase_form.html', {'suppliers': suppliers, 'products': products})

@login_required
def stock_entry_scanner(request):
    if request.method == 'POST':
        barcode = request.POST.get('barcode')
        qty = parse_decimal(request.POST.get('quantity', '1'), default=Decimal('0'))
        if qty <= 0:
            messages.error(request, "La cantidad debe ser mayor a cero.")
            return redirect('products:stock_entry_scanner')
        
        product = Product.objects.filter(Q(barcode=barcode) | Q(sku=barcode)).first()
        
        if product:
            with transaction.atomic():
                product = Product.objects.select_for_update().get(pk=product.pk)
                if product.is_discrete and (qty % Decimal('1')) != Decimal('0'):
                    messages.error(request, f"El producto '{product.name}' es de unidad entera y no admite decimales.")
                    return redirect('products:stock_entry_scanner')
                product.stock += qty
                product.save()
                
                InventoryMovement.objects.create(
                    product=product,
                    quantity=qty,
                    movement_type='PURCHASE_IN',
                    reference="Ingreso por Escáner",
                    reason="Ingreso rápido por Escáner",
                    user=request.user
                )
            messages.success(request, f"Se sumaron {qty} unidades a {product.name}. Nuevo stock: {product.stock}")
        else:
            messages.error(request, f"Producto no encontrado: {barcode}")
            
        return redirect('products:stock_entry_scanner')
        
    return render(request, 'products/stock_entry_scanner.html')
