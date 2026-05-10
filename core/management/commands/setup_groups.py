from django.core.management.base import BaseCommand
from django.contrib.auth.models import Group, Permission
from django.contrib.contenttypes.models import ContentType
from sales.models import Sale
from products.models import Product
from finance.models import CashSession

class Command(BaseCommand):
    help = 'Crea los grupos de usuarios por defecto y asigna permisos básicos'

    def handle(self, *args, **kwargs):
        # 1. Crear Grupos
        cajero_group, _ = Group.objects.get_or_create(name='Cajeros')
        encargado_group, _ = Group.objects.get_or_create(name='Encargados')
        admin_group, _ = Group.objects.get_or_create(name='Administradores')

        # 2. Permisos para Cajero (Solo ventas y caja)
        sale_ct = ContentType.objects.get_for_model(Sale)
        cash_ct = ContentType.objects.get_for_model(CashSession)
        
        cajero_perms = [
            Permission.objects.get(codename='add_sale', content_type=sale_ct),
            Permission.objects.get(codename='view_sale', content_type=sale_ct),
            Permission.objects.get(codename='add_cashsession', content_type=cash_ct),
            Permission.objects.get(codename='view_cashsession', content_type=cash_ct),
        ]
        cajero_group.permissions.set(cajero_perms)

        # 3. Permisos para Encargado (Ventas, Caja, Productos, Clientes)
        product_ct = ContentType.objects.get_for_model(Product)
        encargado_perms = list(cajero_perms) + [
            Permission.objects.get(codename='add_product', content_type=product_ct),
            Permission.objects.get(codename='change_product', content_type=product_ct),
            Permission.objects.get(codename='view_product', content_type=product_ct),
        ]
        encargado_group.permissions.set(encargado_perms)

        # 4. Administrador (Todo)
        # En Django, el administrador suele ser superusuario, pero le damos acceso total al grupo por si acaso
        admin_perms = Permission.objects.all()
        admin_group.permissions.set(admin_perms)

        self.stdout.write(self.style.SUCCESS('Grupos y permisos configurados correctamente.'))
