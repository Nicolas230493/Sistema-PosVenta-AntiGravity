from django.db import migrations

def create_default_customer(apps, schema_editor):
    Customer = apps.get_model('customers', 'Customer')
    Customer.objects.get_or_create(
        dni_cuit='00000000',
        defaults={'full_name': 'Consumidor Final'}
    )

class Migration(migrations.Migration):

    dependencies = [
        ('customers', '0004_customer_points'), # Check the last migration name in audit
    ]

    operations = [
        migrations.RunPython(create_default_customer),
    ]
