from django.db.models.signals import post_save
from django.dispatch import receiver
from django.db import transaction
from .models import Sale, SaleDetail
from core.models import ActivityLog
from customers.models import CurrentAccount
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

        # 3. Cuenta Corriente
        if instance.payment_method == 'CC' and instance.customer:
            with transaction.atomic():
                instance.customer.balance += instance.total_amount
                instance.customer.save()
                
                CurrentAccount.objects.create(
                    customer=instance.customer,
                    amount=instance.total_amount,
                    entry_type='DEBT',
                    reference=f"Venta #{instance.id}",
                    balance_after=instance.customer.balance
                )

# Eliminamos update_stock_on_sale porque pos_view ya descuenta stock y crea InventoryMovement.
# Esto evita que se descuente el doble.
