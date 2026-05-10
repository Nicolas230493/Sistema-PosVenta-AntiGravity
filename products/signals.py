from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver
from .models import Product, PriceLog, StockLoss
from core.models import ActivityLog
import threading

# Objeto local al hilo para capturar el usuario actual en signals (opcional, pero util si se implementa middleware)
_thread_locals = threading.local()

@receiver(pre_save, sender=Product)
def log_price_change(sender, instance, **kwargs):
    if instance.pk:
        try:
            old_product = Product.objects.get(pk=instance.pk)
            if old_product.price != instance.price:
                PriceLog.objects.create(
                    product=instance,
                    old_price=old_product.price,
                    new_price=instance.price,
                    user=getattr(_thread_locals, 'user', None)
                )
                ActivityLog.objects.create(
                    user=getattr(_thread_locals, 'user', None),
                    action=f"Cambio de precio: {instance.name} (${old_product.price} -> ${instance.price})",
                    module="Productos"
                )
        except Product.DoesNotExist:
            pass

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
