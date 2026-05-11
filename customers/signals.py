from django.db.models.signals import post_save
from django.dispatch import receiver
from .models import Payment, CurrentAccount, Customer
from sales.models import Sale

@receiver(post_save, sender=Sale)
def record_debt(sender, instance, created, **kwargs):
    if created and instance.payment_method == 'CC' and instance.customer:
        CurrentAccount.objects.create(
            customer=instance.customer,
            amount=instance.total_amount,
            entry_type='DEBT',
            reference=f"Venta #{instance.id}",
            balance_after=instance.customer.balance # El saldo ya fue actualizado por el signal en sales/signals.py
        )

@receiver(post_save, sender=Payment)
def record_payment(sender, instance, created, **kwargs):
    if created:
        CurrentAccount.objects.create(
            customer=instance.customer,
            amount=instance.amount,
            entry_type='CREDIT',
            reference=f"Pago #{instance.id}",
            balance_after=instance.customer.balance
        )
