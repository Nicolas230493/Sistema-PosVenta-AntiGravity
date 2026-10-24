from contextlib import contextmanager
import threading

from django.core.exceptions import PermissionDenied
from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver

from core.decorators import es_administrador
from core.models import ActivityLog
from .models import Product, PriceLog, StockLoss

# Usuario y motivo del pedido en curso (los completa core.middleware.CurrentUserMiddleware)
_thread_locals = threading.local()

PRICE_CHANGE_DENIED = "Solo el superadministrador o el dueño del negocio pueden cambiar precios."


def set_current_user(user):
    _thread_locals.user = user


def get_current_user():
    return getattr(_thread_locals, 'user', None)


def can_change_prices(user):
    """Cambian precios el superadministrador y el dueño (grupo Administrador/Administradores)."""
    return user is None or es_administrador(user)


@contextmanager
def price_change_reason(reason):
    """Motivo que queda en el historial de precios para los cambios hechos dentro del bloque."""
    previous = getattr(_thread_locals, 'reason', '')
    _thread_locals.reason = reason
    try:
        yield
    finally:
        _thread_locals.reason = previous


@receiver(pre_save, sender=Product)
def log_price_change(sender, instance, **kwargs):
    """
    Historial de precios de venta con usuario y motivo. Fuera de un pedido (comandos, procesos
    del sistema) no hay usuario; dentro de uno, solo un administrador puede cambiar el precio.
    """
    if not instance.pk:
        return
    old = Product.objects.filter(pk=instance.pk).values('price').first()
    if old is None or old['price'] == instance.price:
        return
    user = get_current_user()
    if not can_change_prices(user):
        raise PermissionDenied(PRICE_CHANGE_DENIED)
    PriceLog.objects.create(
        product=instance,
        old_price=old['price'],
        new_price=instance.price,
        price_type='SALE',
        reason=getattr(_thread_locals, 'reason', '') or 'Cambio manual de precio',
        user=user,
    )
    ActivityLog.objects.create(
        user=user,
        action=f"Cambio de precio: {instance.name} (${old['price']} -> ${instance.price})",
        module="Productos"
    )

@receiver(post_save, sender=StockLoss)
def log_stock_loss(sender, instance, created, **kwargs):
    if created:
        # El stock ya se descuenta en la vista stock_loss_create, 
        # junto con la creación del movimiento de inventario.
        
        ActivityLog.objects.create(
            user=instance.user,
            action=f"Baja de stock: {instance.product.name} ({instance.quantity} uni.) - Motivo: {instance.get_reason_display()}",
            module="Inventario"
        )
