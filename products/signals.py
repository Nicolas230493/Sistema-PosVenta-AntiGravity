from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver
from .models import Product, PriceLog
from sales.models import Sale

@receiver(post_save, sender=Sale)
def update_customer_points(sender, instance, created, **kwargs):
    if created and instance.customer:
        # 1 punto cada $1000 de venta
        new_points = int(instance.total_amount // 1000)
        if new_points > 0:
            instance.customer.points += new_points
            instance.customer.save()

@receiver(pre_save, sender=Product)
def log_price_change(sender, instance, **kwargs):
    if instance.pk:
        try:
            old_instance = Product.objects.get(pk=instance.pk)
            if old_instance.price != instance.price:
                PriceLog.objects.create(
                    product=instance,
                    old_price=old_instance.price,
                    new_price=instance.price
                )
        except Product.DoesNotExist:
            pass
