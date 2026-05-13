from django.db.models.signals import post_save
from django.dispatch import receiver
from django.db import transaction
from .models import Sale, SaleDetail, SaleReturn, SaleReturnDetail
from core.models import ActivityLog
from customers.models import CurrentAccount
from products.models import InventoryMovement
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
        if instance.payment_method and instance.payment_method.name == 'Cuenta Corriente' and instance.customer:
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

            # 2. Reversión de Cuenta Corriente (si aplica)
            if instance.sale.customer and instance.sale.payment_method and instance.sale.payment_method.name == 'Cuenta Corriente':
                instance.sale.customer.balance -= instance.total_amount
                instance.sale.customer.save()
                
                CurrentAccount.objects.create(
                    customer=instance.sale.customer,
                    amount=instance.total_amount,
                    entry_type='CREDIT',
                    reference=f"Devolución #{instance.id}",
                    balance_after=instance.sale.customer.balance
                )

@receiver(post_save, sender=SaleReturnDetail)
def update_stock_on_return(sender, instance, created, **kwargs):
    if created:
        # Reingresar stock
        product = instance.product
        product.stock += instance.quantity
        product.save()

        # Registrar movimiento de inventario
        InventoryMovement.objects.create(
            product=product,
            quantity=instance.quantity,
            movement_type='IN',
            reference=f"Devolución #{instance.sale_return.id}",
            user=instance.sale_return.user
        )
