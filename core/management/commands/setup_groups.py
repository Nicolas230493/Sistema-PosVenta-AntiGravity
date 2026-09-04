from django.core.management.base import BaseCommand
from django.contrib.auth.models import Group, Permission
from django.contrib.contenttypes.models import ContentType

class Command(BaseCommand):
    help = 'Configura los grupos y permisos iniciales para el sistema.'

    def handle(self, *args, **kwargs):
        # Grupos
        admin_group, _ = Group.objects.get_or_create(name='Administrador')
        cajero_group, _ = Group.objects.get_or_create(name='Cajero')

        # Permisos básicos
        # Aquí puedes definir permisos granulares si es necesario.
        # Ejemplo: admin_group.permissions.add(...)
        
        self.stdout.write(self.style.SUCCESS('Grupos "Administrador" y "Cajero" configurados correctamente.'))
