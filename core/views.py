from django.shortcuts import render
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.utils import timezone
from datetime import timedelta
from django.db.models import F, Sum, Count
from products.models import Product, StockLoss
from sales.models import Sale, SaleDetail
from django.db.models.functions import ExtractHour
import urllib.parse
import json

@login_required
def dashboard_view(request):
    today = timezone.localdate()
    next_week = today + timedelta(days=7)
    
    # 1. Alertas Críticas
    agotados = Product.objects.filter(stock__lte=0)
    por_reponer = Product.objects.filter(stock__gt=0, stock__lte=F('min_stock'))
    vencimientos_7_dias = Product.objects.filter(expiry_date__range=[today, next_week]).order_by('expiry_date')
    
    if vencimientos_7_dias.exists():
        messages.error(request, f"¡URGENTE! Hay {vencimientos_7_dias.count()} productos por vencer en menos de una semana.")

    # 2. Resumen del Día para WhatsApp
    sales_today = Sale.objects.filter(date__date=today)
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
    cat_sales = SaleDetail.objects.filter(sale__date__date=today).values('product__category__name').annotate(total=Sum('subtotal'))
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
    sales_by_hour = Sale.objects.filter(date__gte=thirty_days_ago).annotate(
        hour=ExtractHour('date')
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
        'vencimientos_7_dias': vencimientos_7_dias,
        'wa_url': wa_url,
        'bajas_recientes': bajas_recientes[:10],
        'hour_labels': json.dumps(hour_labels),
        'hour_data': json.dumps(hour_data),
        'totales': {
            'agotados': agotados.count(),
            'reponer': por_reponer.count(),
            'vencen': vencimientos_7_dias.count(),
            'bajas_mes': bajas_recientes.count(),
            'ventas_hoy': float(total_revenue)
        }
    }

    return render(request, 'core/dashboard.html', context)
