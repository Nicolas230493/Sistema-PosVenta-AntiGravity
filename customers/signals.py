from django.db.models.signals import post_save
from django.dispatch import receiver
from .models import Payment, CurrentAccount

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
