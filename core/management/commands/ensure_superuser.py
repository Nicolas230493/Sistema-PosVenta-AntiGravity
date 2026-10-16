import os

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = 'Crea el superusuario de forma idempotente a partir de variables de entorno.'

    def handle(self, *args, **kwargs):
        username = os.getenv('DJANGO_SUPERUSER_USERNAME', 'admin')
        email = os.getenv('DJANGO_SUPERUSER_EMAIL', 'admin@impulsosmart.com')
        password = os.getenv('DJANGO_SUPERUSER_PASSWORD')

        User = get_user_model()
        if User.objects.filter(username=username).exists():
            self.stdout.write(f"Superusuario '{username}' ya existe. Sin cambios.")
            return

        if not password:
            # Default temporal: debe cambiarse inmediatamente después del primer ingreso.
            password = 'admin'
            self.stdout.write(self.style.WARNING(
                "DJANGO_SUPERUSER_PASSWORD no definida: se usó una contraseña temporal. Cámbiela ya."
            ))

        User.objects.create_superuser(username=username, email=email, password=password)
        self.stdout.write(self.style.SUCCESS(f"Superusuario '{username}' creado."))
