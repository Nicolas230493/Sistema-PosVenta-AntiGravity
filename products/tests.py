import json
from decimal import Decimal
from io import BytesIO

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from openpyxl import load_workbook

from suppliers.models import Supplier
from .models import Product, Purchase, Category, InventoryMovement


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

    def test_stress_latin1_csv_with_broken_price_is_isolated(self):
        """SPEC-QA-13 §13.2: latin-1, ';', '$ 4.500,50' y una fila con 'PRECIO_ROTO' en el medio."""
        csv_text = (
            "Código;Nombre;Precio Venta;Precio Costo;Stock;Unidad;Categoría\n"
            "PECH1;Pechuga de Pollo Ñandú;$ 4.500,50;$ 3.100,25;12,750;kg;Pollería\n"
            "ROTO1;Pata Muslo Señorial;PRECIO_ROTO;$ 2.000,00;5;kg;Pollería\n"
            "ALIT1;Alitas Campiñas;$ 2.999,99;$ 1.800,10;0,000;kg;Pollería\n"
        )
        response = self._upload(csv_text, encoding='latin-1')

        # Post/Redirect/Get: el POST responde 302 al listado (nunca 500) y la página final 200
        self.assertEqual(response.redirect_chain, [(reverse('products:product_list'), 302)])
        self.assertEqual(response.status_code, 200)
        pechuga = Product.objects.get(sku='PECH1')
        self.assertEqual(pechuga.name, 'Pechuga de Pollo Ñandú')
        self.assertEqual(pechuga.price, Decimal('4500.50'))
        self.assertEqual(pechuga.cost_price, Decimal('3100.25'))
        self.assertEqual(pechuga.stock, Decimal('12.750'))
        self.assertEqual(pechuga.unit, 'kg')
        self.assertEqual(pechuga.category.name, 'Pollería')
        alitas = Product.objects.get(sku='ALIT1')
        self.assertEqual(alitas.name, 'Alitas Campiñas')
        self.assertEqual(alitas.price, Decimal('2999.99'))
        self.assertEqual(alitas.stock, Decimal('0.000'))

        self.assertFalse(Product.objects.filter(sku='ROTO1').exists())
        warnings = [str(m) for m in response.context['messages']]
        self.assertTrue(any(w.startswith('Fila 3 omitida') and 'PRECIO_ROTO' in w for w in warnings))
        self.assertEqual(sum(w.startswith('Fila') for w in warnings), 1)
        self.assertTrue(any('2 productos creados' in w for w in warnings))

    def test_inventory_history_filters_forced_adjustments_by_day(self):
        """SPEC-UI-12 §12.2: historial filtrado por AUTO_ADJUST_IN del día con total por producto."""
        alitas = Product.objects.create(sku='ALI', name='Alitas', unit='kg', price=Decimal('3000'))
        InventoryMovement.objects.create(product=alitas, quantity=Decimal('1.250'), movement_type='AUTO_ADJUST_IN', reference='Venta #1')
        InventoryMovement.objects.create(product=alitas, quantity=Decimal('0.750'), movement_type='AUTO_ADJUST_IN', reference='Venta #2')
        InventoryMovement.objects.create(product=alitas, quantity=Decimal('2.000'), movement_type='OUT', reference='Venta #2')

        from django.utils import timezone
        response = self.client.get(reverse('products:inventory_history'), {
            'movement_type': 'AUTO_ADJUST_IN', 'date': timezone.localdate().isoformat(),
        })

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context['movements']), 2)
        totals = list(response.context['product_totals'])
        self.assertEqual(totals[0]['total'], Decimal('2.000'))

        # Tipo inválido o fecha inexistente: se ignoran sin error
        response = self.client.get(reverse('products:inventory_history'), {'movement_type': 'X', 'date': '2026-02-31'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context['movements']), 3)

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


class StockControlIntegralSpecTests(TestCase):
    """Pruebas exhaustivas para la especificación SPEC-AUD-STOCK-06."""

    def setUp(self):
        self.user = User.objects.create_user(username='stock_mgr', password='pass12345', is_staff=True)
        self.client.force_login(self.user)
        self.supplier = Supplier.objects.create(name='Avícola Granja', email='granja@example.com')
        self.category = Category.objects.create(name='Pollería')

    def test_discrete_vs_fractional_unit_validation(self):
        from django.core.exceptions import ValidationError
        # 1. Unidad discreta 'unidad': no admite stock decimal
        prod_discrete = Product(
            name='Pollo Entero',
            unit='unidad',
            price=Decimal('5000.00'),
            cost_price=Decimal('3500.00'),
            stock=Decimal('5.500'),
            min_stock=Decimal('2.000'),
        )
        with self.assertRaises(ValidationError):
            prod_discrete.clean()

        # Unidad discreta con enteros es válida
        prod_discrete.stock = Decimal('5.000')
        prod_discrete.clean()
        prod_discrete.save()
        self.assertTrue(prod_discrete.is_discrete)
        self.assertFalse(prod_discrete.is_fractional)

        # 2. Unidades fraccionables ('kg', 'litro') admiten decimales
        prod_kg = Product(
            name='Pechuga Deshuesada',
            unit='kg',
            price=Decimal('6200.00'),
            cost_price=Decimal('4200.00'),
            stock=Decimal('3.450'),
            min_stock=Decimal('1.500'),
        )
        prod_kg.clean()
        prod_kg.save()
        self.assertTrue(prod_kg.is_fractional)
        self.assertEqual(prod_kg.stock, Decimal('3.450'))

        prod_litro = Product(
            name='Aceite Comestible',
            unit='litro',
            price=Decimal('2200.00'),
            cost_price=Decimal('1600.00'),
            stock=Decimal('10.750'),
            min_stock=Decimal('2.000'),
        )
        prod_litro.clean()
        prod_litro.save()
        self.assertTrue(prod_litro.is_fractional)

    def test_classification_and_traceability_constraints(self):
        # Category FK, optional unique SKU, indexed barcode, supplier SET_NULL
        p1 = Product.objects.create(
            name='Milanesas de Pollo',
            sku=None,
            barcode='779000111222',
            category=self.category,
            supplier=self.supplier,
            price=Decimal('4500.00'),
            cost_price=Decimal('3000.00'),
            stock=Decimal('10'),
        )
        p2 = Product.objects.create(
            name='Alitas de Pollo',
            sku=None, # SKU opcional múltiple None
            barcode='779000111333',
            category=self.category,
            supplier=self.supplier,
            price=Decimal('2500.00'),
            cost_price=Decimal('1800.00'),
            stock=Decimal('8'),
        )
        self.assertIsNone(p1.sku)
        self.assertIsNone(p2.sku)
        self.assertEqual(p1.category, self.category)

        # Borrado del proveedor aplica SET_NULL sin eliminar productos
        self.supplier.delete()
        p1.refresh_from_db()
        self.assertIsNone(p1.supplier)

    def test_price_structure_max_digits_and_separation(self):
        prod = Product.objects.create(
            name='Pollo Premium Exportación',
            sku='EXP001',
            price=Decimal('123456789.99'),
            cost_price=Decimal('98765432.50'),
            stock=Decimal('1'),
        )
        self.assertEqual(prod.price, Decimal('123456789.99'))
        self.assertEqual(prod.cost_price, Decimal('98765432.50'))

    def test_supplier_remito_entry_increases_stock_with_purchase_in(self):
        sup = Supplier.objects.create(name='Distribuidora Mayorista')
        prod = Product.objects.create(
            name='Cajón de Pollo 20kg',
            unit='kg',
            price=Decimal('40000.00'),
            cost_price=Decimal('28000.00'),
            stock=Decimal('20.000'),
            supplier=sup,
        )
        response = self.client.post(reverse('suppliers:supplier_remito_entry'), {
            'supplier': str(sup.id),
            'product': str(prod.id),
            'remito_number': 'R-0001-9988',
            'quantity': '40.000',
            'cost_price': '29000.00',
            'notes': 'Entrada matutina de mercadería',
        }, follow=True)

        self.assertEqual(response.status_code, 200)
        prod.refresh_from_db()
        self.assertEqual(prod.stock, Decimal('60.000'))
        # SPEC-SUP-07 §7.1 CPP: (20 x 28000 + 40 x 29000) / 60
        self.assertEqual(prod.cost_price, Decimal('28666.67'))

        # Verificamos registro en InventoryMovement con PURCHASE_IN
        mov = InventoryMovement.objects.filter(product=prod, movement_type='PURCHASE_IN').first()
        self.assertIsNotNone(mov)
        self.assertEqual(mov.quantity, Decimal('40.000'))
        self.assertIn('R-0001-9988', mov.reference)
        self.assertEqual(mov.user, self.user)
        self.assertEqual(mov.type, 'PURCHASE_IN')

    def test_manual_stock_adjustment_requires_reason(self):
        prod = Product.objects.create(
            name='Pata Muslo',
            unit='kg',
            price=Decimal('3500.00'),
            cost_price=Decimal('2200.00'),
            stock=Decimal('15.000'),
        )
        # Intentar ajustar sin motivo debe fallar por validación del formulario
        res_fail = self.client.post(reverse('products:stock_adjust'), {
            'product': str(prod.id),
            'adjustment_type': 'SUB',
            'quantity': '2.500',
            'reason': '',
        })
        self.assertEqual(res_fail.status_code, 200)
        prod.refresh_from_db()
        self.assertEqual(prod.stock, Decimal('15.000')) # Sin cambios

        # Ajuste con motivo obligatorio registrado
        res_ok = self.client.post(reverse('products:stock_adjust'), {
            'product': str(prod.id),
            'adjustment_type': 'SUB',
            'quantity': '2.500',
            'reason': 'Merma por desposte matutino',
        }, follow=True)
        self.assertEqual(res_ok.status_code, 200)
        prod.refresh_from_db()
        self.assertEqual(prod.stock, Decimal('12.500'))

        mov = InventoryMovement.objects.filter(product=prod, movement_type='ADJ').first()
        self.assertIsNotNone(mov)
        self.assertEqual(mov.quantity, Decimal('2.500'))
        self.assertEqual(mov.reason, 'Merma por desposte matutino')
        self.assertEqual(mov.user, self.user)

    def test_critical_stock_alerts_logic(self):
        p_agotado = Product.objects.create(
            name='Suprema', price=Decimal('5000'), stock=Decimal('0'), min_stock=Decimal('5')
        )
        p_reponer = Product.objects.create(
            name='Menudos', price=Decimal('1000'), stock=Decimal('3'), min_stock=Decimal('5')
        )
        p_ok = Product.objects.create(
            name='Hamburguesas', price=Decimal('3000'), stock=Decimal('15'), min_stock=Decimal('5')
        )

        self.assertEqual(p_agotado.stock_status, 'danger')
        self.assertEqual(p_reponer.stock_status, 'warning')
        self.assertEqual(p_ok.stock_status, 'success')

        # Filtros en la vista product_list
        res_danger = self.client.get(reverse('products:product_list') + '?status=danger')
        self.assertContains(res_danger, p_agotado.name)
        self.assertNotContains(res_danger, p_ok.name)

        res_warning = self.client.get(reverse('products:product_list') + '?status=warning')
        self.assertContains(res_warning, p_reponer.name)
        self.assertNotContains(res_warning, p_ok.name)

    def test_kardex_view_by_product(self):
        prod = Product.objects.create(
            name='Chorizo de Pollo', price=Decimal('2800'), stock=Decimal('10')
        )
        InventoryMovement.objects.create(
            product=prod,
            quantity=Decimal('10.000'),
            movement_type='PURCHASE_IN',
            reference='Remito Inicial',
            reason='Carga inicial de fábrica',
            user=self.user,
        )
        InventoryMovement.objects.create(
            product=prod,
            quantity=Decimal('2.000'),
            movement_type='OUT',
            reference='Venta #1',
            reason='Venta mostrador',
            user=self.user,
        )

        response = self.client.get(reverse('products:product_kardex', kwargs={'pk': prod.pk}))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Chorizo de Pollo')
        self.assertContains(response, 'Remito Inicial')
        self.assertContains(response, 'Carga inicial de fábrica')
        self.assertContains(response, 'Venta #1')

    def test_inventory_exports_csv_excel_and_pdf(self):
        Product.objects.create(
            sku='EXP_TEST',
            name='Producto Exportable',
            price=Decimal('1000.00'),
            cost_price=Decimal('600.00'),
            stock=Decimal('4.000'),
            min_stock=Decimal('5.000'), # Stock bajo
        )

        # 1. Excel (completo y filtrado)
        res_excel = self.client.get(reverse('products:export_inventory_excel'))
        self.assertEqual(res_excel.status_code, 200)
        self.assertEqual(res_excel['Content-Type'], 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')

        res_excel_low = self.client.get(reverse('products:export_inventory_excel') + '?low_stock=1')
        self.assertEqual(res_excel_low.status_code, 200)

        # 2. CSV (completo y filtrado)
        res_csv = self.client.get(reverse('products:export_inventory_csv'))
        self.assertEqual(res_csv.status_code, 200)
        self.assertIn('text/csv', res_csv['Content-Type'])
        self.assertIn('Producto Exportable', res_csv.content.decode('utf-8-sig'))

        res_csv_low = self.client.get(reverse('products:export_inventory_csv') + '?low_stock=1')
        self.assertEqual(res_csv_low.status_code, 200)
        self.assertIn('Producto Exportable', res_csv_low.content.decode('utf-8-sig'))

        # 3. PDF Valorizado (completo y filtrado)
        res_pdf = self.client.get(reverse('products:export_inventory_pdf'))
        self.assertEqual(res_pdf.status_code, 200)
        self.assertEqual(res_pdf['Content-Type'], 'application/pdf')

        res_pdf_low = self.client.get(reverse('products:export_inventory_pdf') + '?low_stock=1')
        self.assertEqual(res_pdf_low.status_code, 200)
        self.assertEqual(res_pdf_low['Content-Type'], 'application/pdf')


class PurchaseSuggestionTests(TestCase):
    """SPEC-INV-09 §9.1: Sugerencia = max(0, V14 x Días Cobertura - Stock)."""

    def setUp(self):
        from django.utils import timezone
        from datetime import timedelta
        from sales.models import Sale, SaleDetail
        self.user = User.objects.create_user(username='compras2', password='pass12345', is_staff=True)
        self.client.force_login(self.user)
        self.supplier = Supplier.objects.create(name='Avícola')
        self.alas = Product.objects.create(name='Alitas', unit='kg', price=Decimal('100'), cost_price=Decimal('50'),
                                           stock=Decimal('3'), supplier=self.supplier)
        self.huevos = Product.objects.create(name='Huevos', price=Decimal('10'), stock=Decimal('500'), supplier=self.supplier)
        sale = Sale.objects.create(user=self.user, total_amount=Decimal('1'))
        SaleDetail.objects.create(sale=sale, product=self.alas, quantity=Decimal('28'), price=Decimal('100'))
        SaleDetail.objects.create(sale=sale, product=self.huevos, quantity=Decimal('14'), price=Decimal('10'))
        # Ventas viejas (fuera de la ventana de 14 días) y anuladas no cuentan
        old = Sale.objects.create(user=self.user, total_amount=Decimal('1'))
        Sale.objects.filter(pk=old.pk).update(fecha_hora=timezone.now() - timedelta(days=20))
        SaleDetail.objects.create(sale=old, product=self.alas, quantity=Decimal('1000'), price=Decimal('100'))
        cancelled = Sale.objects.create(user=self.user, total_amount=Decimal('1'), status='CANCELLED')
        SaleDetail.objects.create(sale=cancelled, product=self.alas, quantity=Decimal('1000'), price=Decimal('100'))

    def test_suggestion_formula(self):
        from .services import purchase_suggestions
        groups = purchase_suggestions(coverage_days=7)

        self.assertEqual(len(groups), 1)
        items = {i['product'].name: i for i in groups[0]['items']}
        # V14 alitas = 28/14 = 2 kg/día; 2 x 7 - 3 = 11. Huevos: 1/día x 7 - 500 < 0 -> sin sugerencia
        self.assertEqual(items['Alitas']['velocity'], Decimal('2.000'))
        self.assertEqual(items['Alitas']['suggested'], 11)
        self.assertNotIn('Huevos', items)

    def test_order_assistant_generates_purchase_order(self):
        from .models import PurchaseOrderDetail
        response = self.client.post(reverse('products:order_assistant'), {'dias': '10'})

        self.assertEqual(response.status_code, 302)
        detail = PurchaseOrderDetail.objects.get()
        self.assertEqual(detail.product, self.alas)
        self.assertEqual(detail.quantity, 17)  # 2 x 10 - 3
        self.assertEqual(detail.order.supplier, self.supplier)
