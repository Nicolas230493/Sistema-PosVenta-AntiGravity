from django.shortcuts import render
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.utils import timezone
from datetime import timedelta
from django.db.models import F
from products.models import Product, StockLoss

@login_required
def dashboard_view(request):
    today = timezone.localdate()
    next_week = today + timedelta(days=7)

    # ... consultas existentes ...
    agotados = Product.objects.filter(stock__lte=0)
    por_reponer = Product.objects.filter(stock__gt=0, stock__lte=F('min_stock'))
    vencen_pronto = Product.objects.filter(expiry_date__range=[today, next_week]).order_by('expiry_date')
    
    # Bajas recientes (últimos 30 días)
    last_month = today - timedelta(days=30)
    bajas_recientes = StockLoss.objects.filter(date__gte=last_month).select_related('product').order_by('-date')

    # ... sistema de mensajería ...
    
    context = {
        'agotados': agotados,
        'por_reponer': por_reponer,
        'vencen_pronto': vencen_pronto,
        'bajas_recientes': bajas_recientes[:10], # Solo las últimas 10 para el resumen
        'totales': {
            'agotados': agotados.count(),
            'reponer': por_reponer.count(),
            'vencen': vencen_pronto.count(),
            'bajas_mes': bajas_recientes.count(),
        }
    }
    
    return render(request, 'core/dashboard.html', context)
