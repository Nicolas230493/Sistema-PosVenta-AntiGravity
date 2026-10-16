from django.core.management.base import BaseCommand
from django.contrib.auth.models import Group, Permission


class Command(BaseCommand):
    help = 'Configura los grupos de roles iniciales para el sistema (idempotente).'

    def handle(self, *args, **kwargs):
        add_sale = Permission.objects.filter(content_type__app_label='sales', codename='add_sale').first()
        for name in ('Administrador', 'Encargados', 'Cajero'):
            group, _ = Group.objects.get_or_create(name=name)
            # Todos los roles operativos pueden vender en el POS
            if add_sale:
                group.permissions.add(add_sale)

        self.stdout.write(self.style.SUCCESS('Grupos "Administrador", "Encargados" y "Cajero" configurados correctamente.'))
