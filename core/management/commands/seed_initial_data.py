from django.core.management.base import BaseCommand

from products.models import Category
from suppliers.models import Supplier

BASE_SUPPLIERS = ['Avícola Granja', 'Panificadora Central', 'Proveedor General']
BASE_CATEGORIES = ['Pollería', 'Panadería', 'Granja', 'Rebozados', 'Otros']


class Command(BaseCommand):
    help = 'Asegura la existencia de proveedores y categorías base (idempotente).'

    def handle(self, *args, **kwargs):
        for name in BASE_SUPPLIERS:
            Supplier.objects.get_or_create(name=name)
        for name in BASE_CATEGORIES:
            Category.objects.get_or_create(name=name)
        self.stdout.write(self.style.SUCCESS('Proveedores y categorías base verificados.'))
