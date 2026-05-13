from django.db import migrations, models
import django.db.models.deletion

def migrate_payment_methods(apps, schema_editor):
    Payment = apps.get_model('customers', 'Payment')
    PaymentMethod = apps.get_model('finance', 'PaymentMethod')
    
    mapping = {
        'CASH': 'Efectivo',
        'TRANS': 'Transferencia',
        'DEBIT': 'Tarjeta de Débito',
        'CREDIT': 'Tarjeta de Crédito',
        'CC': 'Cuenta Corriente',
    }
    
    for old_val, new_name in mapping.items():
        try:
            pm = PaymentMethod.objects.get(name=new_name)
            Payment.objects.filter(payment_method_old=old_val).update(payment_method=pm)
        except PaymentMethod.DoesNotExist:
            pass

class Migration(migrations.Migration):
    dependencies = [
        ('finance', '0005_paymentmethod'),
        ('customers', '0008_currentaccount'),
    ]
    operations = [
        migrations.RenameField(
            model_name='payment',
            old_name='payment_method',
            new_name='payment_method_old',
        ),
        migrations.AlterField(
            model_name='payment',
            name='payment_method_old',
            field=models.CharField(max_length=10, null=True, blank=True),
        ),
        migrations.AddField(
            model_name='payment',
            name='payment_method',
            field=models.ForeignKey(null=True, on_delete=django.db.models.deletion.PROTECT, to='finance.paymentmethod', verbose_name='Método de Pago'),
        ),
        migrations.RunPython(migrate_payment_methods),
    ]
