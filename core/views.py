from django.shortcuts import render
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.utils import timezone
from datetime import timedelta
from django.db.models import F, Sum, Count, Q
from django.http import JsonResponse
from django.urls import reverse
from products.models import Product, StockLoss
from sales.models import Sale, SaleDetail
from customers.models import Customer
from django.db.models.functions import ExtractHour
import urllib.parse
import json

from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.utils import timezone
from datetime import timedelta
from django.db.models import F, Sum, Count, Q
from django.http import JsonResponse
from django.urls import reverse
from products.models import Product, StockLoss
from sales.models import Sale, SaleDetail
from customers.models import Customer
from core.models import TurnoCaja
from django.db.models.functions import ExtractHour
import urllib.parse
import json

from .decorators import es_encargado_o_admin, manager_required
from .forms import ConfiguracionForm
from .models import ActivityLog, ConfiguracionSistema
from products.services import expiring_products
from sales.models import Promotion
from decimal import Decimal, InvalidOperation
from django.db import transaction
from django.shortcuts import get_object_or_404
from django.views.decorators.http import require_POST

EXPIRY_PROMO_PREFIX = "Liquidación por vencimiento"

@login_required
def configuracion_view(request):
    config = ConfiguracionSistema.get_config()
    if request.method == 'POST':
        form = ConfiguracionForm(request.POST, request.FILES, instance=config)
        if form.is_valid():
            form.save()
            messages.success(request, "Configuración actualizada correctamente.")
            return redirect('configuracion')
    else:
        form = ConfiguracionForm(instance=config)
    return render(request, 'core/configuracion.html', {'form': form})


@login_required
def abrir_turno(request):
    if request.method == 'POST':
        monto_inicial = request.POST.get('monto_inicial', 0)
        TurnoCaja.objects.create(usuario=request.user, monto_inicial=monto_inicial)
        messages.success(request, "Turno abierto correctamente.")
        return redirect('dashboard') # Asumiendo URL de dashboard
    return render(request, 'core/abrir_turno.html')

@login_required
def cerrar_turno(request):
    turno = TurnoCaja.objects.filter(usuario=request.user, estado='ABIERTO').first()
    if not turno:
        messages.error(request, "No hay turno abierto.")
        return redirect('dashboard')
    
    if request.method == 'POST':
        # Calcular monto final basado en ventas del turno
        ventas = Sale.objects.filter(user=request.user, turno=turno)
        monto_final = ventas.aggregate(total=Sum('total_amount'))['total'] or 0
        turno.monto_final = monto_final
        turno.estado = 'CERRADO'
        turno.fecha_cierre = timezone.now()
        turno.save()
        messages.success(request, f"Turno cerrado. Total recaudado: ${monto_final}")
        return redirect('dashboard')
    return render(request, 'core/cerrar_turno.html', {'turno': turno})

def cuenta_expirada(request):
    return render(request, 'core/cuenta_expirada.html')

@login_required
def global_search(request):
    q = request.GET.get('q', '')
    results = []
    if len(q) >= 2:
        # Productos
        products = Product.objects.filter(Q(name__icontains=q) | Q(sku__icontains=q))[:5]
        for p in products:
            results.append({
                'title': p.name,
                'category': 'Producto',
                'url': reverse('products:product_update', args=[p.id]),
                'icon': 'bi-box-seam'
            })
        
        # Ventas
        if q.isdigit():
            sales = Sale.objects.filter(id__icontains=q)[:5]
            for s in sales:
                results.append({
                    'title': f"Venta #{s.id}",
                    'category': 'Venta',
                    'url': reverse('sales:sale_list') + f"?q={s.id}",
                    'icon': 'bi-receipt'
                })
            
        # Clientes
        customers = Customer.objects.filter(Q(full_name__icontains=q) | Q(dni_cuit__icontains=q))[:5]
        for c in customers:
            results.append({
                'title': c.full_name,
                'category': 'Cliente',
                'url': reverse('customers:customer_detail', args=[c.id]),
                'icon': 'bi-person'
            })
            
    return JsonResponse({'results': results})

@login_required
def dashboard_view(request):
    today = timezone.localdate()
    next_week = today + timedelta(days=7)
    
    # 1. Alertas Críticas
    agotados = Product.objects.filter(stock__lte=0)
    por_reponer = Product.objects.filter(stock__gt=0, stock__lte=F('min_stock'))
    # SPEC-INV-09 §9.1: vencen dentro de N días configurables o ya vencidos
    dias_alerta = ConfiguracionSistema.get_config().dias_alerta_vencimiento
    vencimientos = list(expiring_products(dias_alerta, today))
    liquidaciones = dict(
        Promotion.objects.filter(
            active=True, promo_type='PERCENT', name__startswith=EXPIRY_PROMO_PREFIX,
            start_date__lte=today, end_date__gte=today,
        ).values_list('products__id', 'discount_percentage')
    )
    for p in vencimientos:
        p.descuento_activo = liquidaciones.get(p.id)

    # 2. Resumen del Día para WhatsApp
    sales_today = Sale.objects.filter(fecha_hora__date=today)
    total_revenue = sales_today.aggregate(total=Sum('total_amount'))['total'] or 0
    sales_count = sales_today.count()
    
    # Desglose de medios de pago (Agrupado por Efectivo vs Digital)
    pay_methods = sales_today.values('payment_method').annotate(total=Sum('total_amount'))
    total_efectivo = 0
    total_digital = 0
    pay_msg = ""
    for p in pay_methods:
        method = p['payment_method']
        amount = p['total']
        pay_msg += f"- {method}: ${amount}\n"
        if method == 'CASH':
            total_efectivo += amount
        else:
            total_digital += amount
    
    # Desglose por Categoría
    cat_sales = SaleDetail.objects.filter(sale__fecha_hora__date=today).values('product__category__name').annotate(total=Sum('subtotal'))
    cat_msg = ""
    for c in cat_sales:
        cat_name = c['product__category__name'] or "Sin Categoría"
        cat_msg += f"- {cat_name}: ${c['total']}\n"
    
    # Mermas del día
    mermas_today = StockLoss.objects.filter(date__date=today).aggregate(total=Sum('quantity'))['total'] or 0
    
    # Construcción de mensaje WhatsApp estructurado
    wa_text = f"📊 *CIERRE DE CAJA - IMPULSO SMART*\n"
    wa_text += f"------------------------------------------\n"
    wa_text += f"📅 *Fecha:* {today.strftime('%d/%m/%Y')}\n"
    wa_text += f"💰 *Total Ventas:* ${total_revenue}\n"
    wa_text += f"🛍️ *Cant. Ventas:* {sales_count}\n"
    wa_text += f"------------------------------------------\n"
    wa_text += f"💵 *Efectivo:* ${total_efectivo}\n"
    wa_text += f"💳 *Digital:* ${total_digital}\n"
    wa_text += f"------------------------------------------\n"
    wa_text += f"📂 *Por Categoría:*\n{cat_msg}"
    wa_text += f"------------------------------------------\n"
    wa_text += f"⚠️ *Mermas:* {mermas_today} unidades\n"
    
    if agotados.exists():
        wa_text += f"🚨 *Faltantes Críticos:* {', '.join([p.name for p in agotados[:5]])}\n"
        
    wa_text += f"------------------------------------------\n"
    wa_text += f"🚀 ¡Día finalizado con éxito!"
    wa_url = f"https://wa.me/?text={urllib.parse.quote(wa_text)}"


    # 3. Gráfico de Horas Pico (Histórico de 30 días para mejor promedio)
    thirty_days_ago = timezone.now() - timedelta(days=30)
    sales_by_hour = Sale.objects.filter(fecha_hora__gte=thirty_days_ago).annotate(
        hour=ExtractHour('fecha_hora')
    ).values('hour').annotate(
        count=Count('id'), 
        total=Sum('total_amount')
    ).order_by('hour')
    
    # Preparar datos para Chart.js (asegurar que todas las horas estén presentes)
    hour_labels = [f"{h:02d}:00" for h in range(24)]
    hour_data = [0] * 24
    for s in sales_by_hour:
        hour_data[s['hour']] = float(s['total'])

    # 4. Contexto para el template
    last_month = today - timedelta(days=30)
    bajas_recientes = StockLoss.objects.filter(date__gte=last_month).select_related('product').order_by('-date')
    
    context = {
        'agotados': agotados,
        'por_reponer': por_reponer,
        'vencimientos': vencimientos,
        'dias_alerta': dias_alerta,
        'descuentos_rapidos': (10, 20, 30, 50),
        'today': today,
        'puede_liquidar': es_encargado_o_admin(request.user),
        'wa_url': wa_url,
        'bajas_recientes': bajas_recientes[:10],
        'hour_labels': json.dumps(hour_labels),
        'hour_data': json.dumps(hour_data),
        'totales': {
            'agotados': agotados.count(),
            'reponer': por_reponer.count(),
            'vencen': len(vencimientos),
            'bajas_mes': bajas_recientes.count(),
            'ventas_hoy': float(total_revenue)
        }
    }

    return render(request, 'core/dashboard.html', context)


@manager_required
@require_POST
def apply_expiry_discount(request, pk):
    """
    SPEC-INV-09 §9.1: descuento promocional rápido para un producto por vencer.
    Crea (o actualiza) una Promoción porcentual vigente hasta la fecha de vencimiento,
    así el precio de lista no se modifica y el descuento caduca solo.
    """
    product = get_object_or_404(Product, pk=pk)
    try:
        percent = Decimal(request.POST.get('percent', ''))
    except InvalidOperation:
        percent = Decimal('0')
    if not (Decimal('0') < percent <= Decimal('90')):
        messages.error(request, "El descuento debe estar entre 1% y 90%.")
        return redirect('dashboard')

    today = timezone.localdate()
    end_date = max(product.expiry_date or today, today)
    with transaction.atomic():
        promo = Promotion.objects.filter(
            active=True, promo_type='PERCENT', name__startswith=EXPIRY_PROMO_PREFIX,
            products=product, end_date__gte=today,
        ).first()
        if promo:
            promo.discount_percentage = percent
            promo.start_date = today
            promo.end_date = end_date
            promo.save()
        else:
            promo = Promotion.objects.create(
                name=f"{EXPIRY_PROMO_PREFIX}: {product.name}"[:100],
                promo_type='PERCENT',
                discount_percentage=percent,
                start_date=today,
                end_date=end_date,
            )
            promo.products.add(product)
        ActivityLog.objects.create(
            user=request.user,
            action=f"Descuento por vencimiento {percent}%: {product.name}",
            module="Inventario",
            details=f"Vence: {product.expiry_date} | Promo #{promo.id} hasta {end_date}",
        )
    messages.success(request, f"Descuento del {percent}% aplicado a {product.name} hasta el {end_date:%d/%m/%Y}.")
    return redirect('dashboard')
