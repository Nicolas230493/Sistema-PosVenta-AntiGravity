from django.shortcuts import render
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.utils import timezone
from datetime import timedelta
from django.db.models import F
from products.models import Product

@login_required
def dashboard_view(request):
    today = timezone.localdate()
    next_week = today + timedelta(days=7)

    # 1. Consultas optimizadas para SQLite
    # Agotados: Stock exactamente en 0 o menos
    agotados = Product.objects.filter(stock__lte=0)
    
    # Por Reponer: Stock mayor a 0 pero menor o igual al mínimo
    por_reponer = Product.objects.filter(stock__gt=0, stock__lte=F('min_stock'))
    
    # Vencen Pronto: Fecha entre hoy y los próximos 7 días
    vencen_pronto = Product.objects.filter(expiry_date__range=[today, next_week]).order_by('expiry_date')

    # 2. Sistema de Mensajería (Solo si hay urgencias)
    if agotados.exists() or vencen_pronto.exists():
        count_agotados = agotados.count()
        count_vencen = vencen_pronto.count()
        
        msg = f"Atención: Tienes {count_agotados} productos agotados"
        if count_vencen > 0:
            msg += f" y {count_vencen} por vencer pronto."
        else:
            msg += "."
            
        messages.warning(request, msg)

    context = {
        'agotados': agotados,
        'por_reponer': por_reponer,
        'vencen_pronto': vencen_pronto,
        'totales': {
            'agotados': agotados.count(),
            'reponer': por_reponer.count(),
            'vencen': vencen_pronto.count(),
        }
    }
    
    return render(request, 'core/dashboard.html', context)
