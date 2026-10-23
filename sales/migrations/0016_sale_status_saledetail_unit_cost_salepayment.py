import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


def backfill(apps, schema_editor):
    """Estado de ventas anuladas y un SalePayment por cada venta previa (pago único)."""
    Sale = apps.get_model('sales', 'Sale')
    SalePayment = apps.get_model('sales', 'SalePayment')

    Sale.objects.filter(is_cancelled=True).update(status='CANCELLED')

    payments = [
        SalePayment(
            sale_id=sale.id,
            payment_method_id=sale.payment_method_id,
            amount=sale.total_amount,
            cash_session_id=sale.cash_session_id,
            user_id=sale.user_id,
        )
        for sale in Sale.objects.filter(payment_method__isnull=False).only(
            'id', 'payment_method_id', 'total_amount', 'cash_session_id', 'user_id'
        ).iterator()
    ]
    SalePayment.objects.bulk_create(payments, batch_size=500)
    # auto_now_add fija la fecha de hoy: se alinea con la fecha de la venta
    for sale_id, fecha in Sale.objects.filter(payment_method__isnull=False).values_list('id', 'fecha_hora').iterator():
        SalePayment.objects.filter(sale_id=sale_id).update(date=fecha)


class Migration(migrations.Migration):

    dependencies = [
        ('finance', '0007_cashexpense_kind_cashsession_declared_cash_and_more'),
        ('sales', '0015_sale_cancel_reason_sale_cancelled_at_and_more'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.RenameField(
            model_name='saledetail',
            old_name='cost_price_at_sale',
            new_name='unit_cost',
        ),
        migrations.AlterField(
            model_name='saledetail',
            name='unit_cost',
            field=models.DecimalField(decimal_places=2, default=0, max_digits=12, verbose_name='Costo Unitario (Histórico)'),
        ),
        migrations.AddField(
            model_name='sale',
            name='status',
            field=models.CharField(choices=[('COMPLETED', 'Completada'), ('RESERVED', 'Apartado / Pedido con Seña'), ('CANCELLED', 'Anulada')], db_index=True, default='COMPLETED', max_length=10, verbose_name='Estado'),
        ),
        migrations.CreateModel(
            name='SalePayment',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('amount', models.DecimalField(decimal_places=2, max_digits=12, verbose_name='Monto')),
                ('reference', models.CharField(blank=True, default='', max_length=100, verbose_name='N° de Comprobante')),
                ('date', models.DateTimeField(auto_now_add=True, verbose_name='Fecha')),
                ('cash_session', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='sale_payments', to='finance.cashsession', verbose_name='Sesión de Caja')),
                ('payment_method', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to='finance.paymentmethod', verbose_name='Método de Pago')),
                ('sale', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='payments', to='sales.sale', verbose_name='Venta')),
                ('user', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to=settings.AUTH_USER_MODEL, verbose_name='Cobrado por')),
            ],
            options={
                'verbose_name': 'Pago de Venta',
                'verbose_name_plural': 'Pagos de Venta',
                'ordering': ['date', 'id'],
            },
        ),
        migrations.RunPython(backfill, migrations.RunPython.noop),
    ]
