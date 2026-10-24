"""Inventario inteligente: vencimientos y sugerencia de compra (SPEC-INV-09)."""
import math
from datetime import timedelta
from decimal import Decimal

from django.db.models import Sum
from django.utils import timezone

from .models import Product

SALES_WINDOW_DAYS = 14
DEFAULT_COVERAGE_DAYS = 7


def expiring_products(days, today=None):
    """Productos con stock que vencen dentro de `days` días o ya vencidos (expiry_date <= hoy + días)."""
    today = today or timezone.localdate()
    return Product.objects.filter(
        active=True,
        stock__gt=0,
        expiry_date__isnull=False,
        expiry_date__lte=today + timedelta(days=days),
    ).select_related('category').order_by('expiry_date')


def daily_sales_velocity(window_days=SALES_WINDOW_DAYS):
    """V14 = total vendido en los últimos 14 días / 14, por producto (excluye ventas anuladas)."""
    from sales.models import Sale, SaleDetail

    since = timezone.now() - timedelta(days=window_days)
    rows = SaleDetail.objects.filter(
        sale__fecha_hora__gte=since, product__isnull=False,
    ).exclude(sale__status=Sale.STATUS_CANCELLED).values('product_id').annotate(total=Sum('quantity'))
    return {row['product_id']: row['total'] / Decimal(window_days) for row in rows}


def purchase_suggestions(coverage_days=DEFAULT_COVERAGE_DAYS):
    """
    SPEC-INV-09 §9.1: Sugerencia = max(0, V14 x Días Cobertura - Stock Actual), agrupada por proveedor.
    Las cantidades se redondean hacia arriba a unidad entera (las OC se piden por unidad/kg entero).
    Devuelve [{'supplier': Supplier|None, 'items': [{'product', 'velocity', 'suggested'}]}].
    """
    velocity = daily_sales_velocity()
    coverage = Decimal(coverage_days)
    groups = {}
    products = Product.objects.filter(id__in=velocity.keys(), active=True).select_related('supplier').order_by('name')
    for product in products:
        v14 = velocity[product.id]
        suggested = max(Decimal('0'), v14 * coverage - product.stock)
        if suggested <= 0:
            continue
        group = groups.setdefault(product.supplier_id, {'supplier': product.supplier, 'items': []})
        group['items'].append({
            'product': product,
            'velocity': v14.quantize(Decimal('0.001')),
            'suggested': math.ceil(suggested),
        })
    return sorted(groups.values(), key=lambda g: (g['supplier'] is None, g['supplier'].name if g['supplier'] else ''))
