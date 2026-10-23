from django.db.models.signals import post_save
from django.dispatch import receiver
from django.db import transaction
from .models import Sale, SaleReturn
from core.models import ActivityLog
from decimal import Decimal

@receiver(post_save, sender=Sale)
def handle_sale_effects(sender, instance, created, **kwargs):
    if created:
        # 1. Registrar Actividad
        ActivityLog.objects.create(
            user=instance.user,
            action=f"Venta confirmada #{instance.id} - Total: ${instance.total_amount}",
            module="Ventas"
        )

        # 2. Fidelización: 1 punto por cada $1000
        if instance.customer:
            points_earned = int(instance.total_amount / Decimal('1000'))
            if points_earned > 0:
                instance.customer.points += points_earned
                instance.customer.save()

        # 3. Cuenta Corriente: se debita en sales.services._register_payments según
        #    los SalePayment (SPEC-POS-08), que aún no existen en este punto.

@receiver(post_save, sender=SaleReturn)
def handle_return_effects(sender, instance, created, **kwargs):
    if created:
        with transaction.atomic():
            # 1. Registrar Actividad
            ActivityLog.objects.create(
                user=instance.user,
                action=f"Devolución procesada #{instance.id} de Venta #{instance.sale.id}",
                module="Ventas"
            )

