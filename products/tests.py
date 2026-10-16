import json
from decimal import Decimal
from io import BytesIO

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from openpyxl import load_workbook

from suppliers.models import Supplier
from .models import Product, Purchase


class ProductStockValidationTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='operator', password='pass12345', is_staff=True)
        self.client.force_login(self.user)
        self.supplier = Supplier.objects.create(name='Proveedor')
        self.product = Product.objects.create(
            sku='PROD1',
            barcode='B001',
            name='Producto',
            price=Decimal('50.00'),
            cost_price=Decimal('25.00'),
            stock=10,
            min_stock=2,
            supplier=self.supplier,
        )

    def test_stock_loss_rejects_negative_quantity(self):
        self.client.post(reverse('products:stock_loss_create'), {
            'product': str(self.product.id),
            'quantity': '-5',
            'reason': 'OTH',
        })

        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, 10)

    def test_purchase_rejects_negative_quantity(self):
        items = [{'product_id': self.product.id, 'qty': '-5', 'cost': '10.00'}]

        self.client.post(reverse('products:purchase_create'), {
            'supplier_id': str(self.supplier.id),
            'invoice_number': 'A-1',
            'items_data': json.dumps(items),
        })

        self.assertEqual(Purchase.objects.count(), 0)
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, 10)

    def test_inventory_excel_exports_valid_workbook_with_barcode_columns(self):
        response = self.client.get(reverse('products:export_inventory_excel'))

        self.assertEqual(response.status_code, 200)
        workbook = load_workbook(BytesIO(response.content))
        sheet = workbook.active
        headers = [cell.value for cell in sheet[1]]
        self.assertIn('sku', headers)
        self.assertIn('barcode', headers)

    def test_advanced_excel_exports_valid_workbook_even_without_data(self):
        Product.objects.all().delete()

        response = self.client.get(reverse('products:export_advanced_excel'))

        self.assertEqual(response.status_code, 200)
        workbook = load_workbook(BytesIO(response.content))
        self.assertGreaterEqual(len(workbook.sheetnames), 1)

    def test_label_export_returns_pdf_for_barcode_product(self):
        response = self.client.get(reverse('products:export_labels'), {
            'products': [str(self.product.id)],
        })

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/pdf')


class CsvImporterTests(TestCase):
    """SPEC-IMP-04: importador tolerante a fallos."""

    def setUp(self):
        self.user = User.objects.create_user(username='importer', password='pass12345', is_staff=True)
        self.client.force_login(self.user)

    def _upload(self, content, name='productos.csv', encoding='utf-8'):
        from django.core.files.uploadedfile import SimpleUploadedFile
        upload = SimpleUploadedFile(name, content.encode(encoding), content_type='text/csv')
        return self.client.post(reverse('products:import_inventory_excel'), {'excel_file': upload}, follow=True)

    def test_parse_decimal_sanitizes_formats(self):
        from .importers import parse_decimal
        self.assertEqual(parse_decimal('$ 1.234,50'), Decimal('1234.50'))
        self.assertEqual(parse_decimal('1,234.50'), Decimal('1234.50'))
        self.assertEqual(parse_decimal('0,350'), Decimal('0.350'))
        self.assertEqual(parse_decimal('1.234.567'), Decimal('1234567'))
        self.assertEqual(parse_decimal(''), Decimal('0.00'))
        self.assertEqual(parse_decimal('abc'), Decimal('0.00'))

    def test_header_normalization(self):
        from .importers import map_header
        self.assertEqual(map_header(' Precio Venta '), 'price')
        self.assertEqual(map_header('precio_venta'), 'price')
        self.assertEqual(map_header('PRECIO'), 'price')
        self.assertEqual(map_header('Categoría'), 'category')

    def test_latin1_semicolon_csv_with_bad_row_continues(self):
        csv_text = (
            "Nombre;Precio Venta;Stock;Categoría;Proveedor\n"
            "Pechuga;$ 5.200,00;3,5;Pollería;Avícola Granja\n"
            "Fila Rota;-10;1;;\n"
            "Medialunas;1500;24;Panadería;\n"
        )
        response = self._upload(csv_text, encoding='latin-1')

        self.assertEqual(response.status_code, 200)
        pechuga = Product.objects.get(name='Pechuga')
        self.assertEqual(pechuga.price, Decimal('5200.00'))
        self.assertEqual(pechuga.stock, Decimal('3.500'))
        self.assertEqual(pechuga.category.name, 'Pollería')
        self.assertTrue(Product.objects.filter(name='Medialunas').exists())
        self.assertFalse(Product.objects.filter(name='Fila Rota').exists())
        warnings = [str(m) for m in response.context['messages']]
        self.assertTrue(any(w.startswith('Fila 3 omitida') for w in warnings))

    def test_comma_csv_updates_existing_product_by_sku(self):
        Product.objects.create(sku='ABC1', name='Huevos', price=Decimal('10'))

        self._upload("sku,precio,stock\nABC1,2500.50,30\n")

        product = Product.objects.get(sku='ABC1')
        self.assertEqual(product.price, Decimal('2500.50'))
        self.assertEqual(product.stock, Decimal('30'))

    def test_products_without_sku_get_unique_sku(self):
        self._upload("nombre,precio\nProducto A,10\nProducto B,20\n")

        skus = set(Product.objects.values_list('sku', flat=True))
        self.assertEqual(len(skus), 2)

    def test_product_supplier_is_optional(self):
        product = Product.objects.create(sku='NOSUP', name='Sin proveedor', price=Decimal('1'))
        self.assertIsNone(product.supplier)
        self.assertTrue(Product._meta.get_field('supplier').blank)
