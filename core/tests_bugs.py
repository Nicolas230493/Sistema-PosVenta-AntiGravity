"""
Regresiones de la revisión de errores (24/10/2026): cada test reproduce un error encontrado
y verifica su corrección.
"""
import json
import urllib.parse
from decimal import Decimal

from django.contrib.auth.models import Group, Permission, User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from customers.models import CurrentAccount, Customer, Payment
from finance.models import CashSession, PaymentMethod
from finance.services import compute_expected
from products.models import Category, InventoryMovement, PriceLog, Product
from sales.models import Promotion, Sale, SaleReturn
from suppliers.models import Supplier


def _admin(username='admin_bugs'):
    user = User.objects.create_user(username=username, password='pass12345', is_staff=True)
    user.groups.add(Group.objects.get_or_create(name='Administrador')[0])
    user.user_permissions.add(Permission.objects.get(codename='add_sale', content_type__app_label='sales'))
    return user


class CustomerPaymentBugTests(TestCase):
    def setUp(self):
        self.user = _admin()
        self.client.force_login(self.user)
        self.cash = PaymentMethod.objects.create(name='Efectivo')
        PaymentMethod.objects.create(name='Cuenta Corriente')
        self.customer = Customer.objects.create(full_name='Marta', dni_cuit='27456789', balance=Decimal('18400.00'))

    def test_payment_page_lists_payment_methods(self):
        """Antes la página no recibía los medios de pago: el selector salía vacío y no se podía cobrar."""
        response = self.client.get(reverse('customers:customer_payment', args=[self.customer.id]))

        self.assertContains(response, f'<option value="{self.cash.id}">Efectivo</option>', html=True)
        self.assertNotContains(response, '>Cuenta Corriente<')

    def test_statement_balance_after_is_the_new_balance(self):
        """Antes el asiento guardaba el saldo previo al pago como 'saldo resultante'."""
        self.client.post(reverse('customers:customer_payment', args=[self.customer.id]), {
            'amount': '10000,50', 'payment_method': str(self.cash.id),
        })

        self.customer.refresh_from_db()
        self.assertEqual(self.customer.balance, Decimal('8399.50'))
        entry = CurrentAccount.objects.get(customer=self.customer, entry_type='CREDIT')
        self.assertEqual(entry.balance_after, Decimal('8399.50'))

    def test_invalid_amount_or_method_does_not_register(self):
        url = reverse('customers:customer_payment', args=[self.customer.id])
        self.client.post(url, {'amount': 'abc', 'payment_method': str(self.cash.id)})
        self.client.post(url, {'amount': '100', 'payment_method': ''})

        self.assertEqual(Payment.objects.count(), 0)

    def test_customer_payment_by_another_user_counts_in_open_cash(self):
        """Antes la caja solo sumaba los cobros a clientes hechos por quien la abrió."""
        cashier = User.objects.create_user(username='lucia', password='pass12345')
        session = CashSession.objects.create(user=cashier, initial_amount=Decimal('1000.00'))
        self.client.post(reverse('customers:customer_payment', args=[self.customer.id]), {
            'amount': '500', 'payment_method': str(self.cash.id),
        })

        self.assertEqual(compute_expected(session)['expected_cash'], Decimal('1500.00'))


class SaleReportsBugTests(TestCase):
    """Las ventas anuladas y los pedidos con seña pendientes no son ventas del día ni se declaran."""

    def setUp(self):
        self.user = User.objects.create_superuser(username='duenio_bugs', password='pass12345')
        self.client.force_login(self.user)
        self.cash = PaymentMethod.objects.create(name='Efectivo')
        self.transfer = PaymentMethod.objects.create(name='Transferencia', is_digital=True)
        CashSession.objects.create(user=self.user, initial_amount=Decimal('0'))
        self.product = Product.objects.create(sku='POL', name='Pollo', unit='kg', price=Decimal('4000'),
                                              cost_price=Decimal('2000'), stock=Decimal('50'))

    def _sell(self, payments):
        response = self.client.post(reverse('sales:pos'), {
            'cart_data': json.dumps([{'id': self.product.id, 'qty': 1, 'weight': 1}]),
            'payment_method': str(self.cash.id),
            'payments_data': json.dumps(payments),
            'price_list': 'default',
        }, headers={'X-POS-Ajax': '1'})
        return Sale.objects.get(pk=response.json()['sale_id'])

    def test_dashboard_excludes_cancelled_and_splits_cash_and_digital(self):
        from sales.services import cancel_sale
        self._sell([{'method_id': self.cash.id, 'amount': '3000'}, {'method_id': self.transfer.id, 'amount': '1000'}])
        cancelled = self._sell([{'method_id': self.cash.id, 'amount': '4000'}])
        cancel_sale(sale=cancelled, user=self.user, reason='Error de carga')

        response = self.client.get(reverse('dashboard'))

        self.assertEqual(response.context['totales']['ventas_hoy'], 4000.0)
        whatsapp = urllib.parse.unquote(response.context['wa_url'])
        self.assertIn('*Efectivo:* $3000', whatsapp)  # antes salía siempre $0
        self.assertIn('*Digital:* $1000', whatsapp)
        self.assertIn('*Cant. Ventas:* 1', whatsapp)

    def test_cash_dashboard_profit_excludes_cancelled(self):
        from sales.services import cancel_sale
        self._sell([{'method_id': self.cash.id, 'amount': '4000'}])
        cancel_sale(sale=self._sell([{'method_id': self.cash.id, 'amount': '4000'}]), user=self.user, reason='x')

        stats = self.client.get(reverse('finance:cash_dashboard')).context['profit_stats']

        self.assertEqual(stats['revenue'], Decimal('4000.00'))
        self.assertEqual(stats['cost'], Decimal('2000.00'))

    def test_fiscal_report_excludes_cancelled_sales(self):
        from sales.services import cancel_sale
        kept = self._sell([{'method_id': self.cash.id, 'amount': '4000'}])
        cancel_sale(sale=self._sell([{'method_id': self.cash.id, 'amount': '4000'}]), user=self.user, reason='x')

        response = self.client.get(reverse('finance:fiscal_reports'))

        self.assertEqual([s.id for s in response.context['sales']], [kept.id])


class SaleReturnBugTests(TestCase):
    def setUp(self):
        self.user = _admin('enc_devol')
        self.client.force_login(self.user)
        self.cash = PaymentMethod.objects.create(name='Efectivo')
        CashSession.objects.create(user=self.user, initial_amount=Decimal('0'))
        self.product = Product.objects.create(sku='PEC', name='Pechuga', unit='kg', price=Decimal('5000'),
                                              stock=Decimal('10'))
        response = self.client.post(reverse('sales:pos'), {
            'cart_data': json.dumps([{'id': self.product.id, 'qty': 1, 'weight': 2}]),
            'payment_method': str(self.cash.id),
            'price_list': 'default',
        }, headers={'X-POS-Ajax': '1'})
        self.sale = Sale.objects.get(pk=response.json()['sale_id'])
        self.detail = self.sale.details.get()

    def _return(self, qty):
        return self.client.post(reverse('sales:sale_return', args=[self.sale.id]), {
            'reason': 'Devolución', f'qty_{self.detail.id}': qty,
        })

    def test_same_item_cannot_be_returned_twice(self):
        """Antes se podían repetir devoluciones del mismo artículo: stock y reintegro inflados."""
        self._return('1.5')
        self._return('1')  # solo queda 0,5 kg por devolver

        self.assertEqual(SaleReturn.objects.count(), 1)
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, Decimal('9.500'))  # 10 - 2 + 1,5

        self._return('0.5')
        self.assertEqual(SaleReturn.objects.count(), 2)
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, Decimal('10.000'))

    def test_product_with_returns_cannot_be_deleted_without_error(self):
        """Antes borrar un producto con devoluciones daba error 500 (ProtectedError); ahora se desactiva."""
        self._return('1')
        superuser = User.objects.create_superuser(username='root_bugs', password='pass12345')
        self.client.force_login(superuser)

        response = self.client.post(reverse('products:product_delete', args=[self.product.id]), follow=True)

        self.assertEqual(response.status_code, 200)
        self.assertFalse(Product.objects.get(pk=self.product.pk).active)
        self.assertTrue(any('desactivado' in str(m) for m in response.context['messages']))


class BusinessIntelligenceBugTests(TestCase):
    def setUp(self):
        self.user = _admin('admin_bi')  # grupo "Administrador" (el que crea setup_groups)
        self.client.force_login(self.user)

    def test_admin_group_can_open_bi_and_charts_receive_valid_json(self):
        """Antes rechazaba al grupo 'Administrador' y volcaba Decimal('…') en el JavaScript."""
        cash = PaymentMethod.objects.create(name='Efectivo')
        CashSession.objects.create(user=self.user, initial_amount=Decimal('0'))
        product = Product.objects.create(sku='A', name='Alitas <b>', unit='kg', price=Decimal('3000'), stock=Decimal('5'))
        self.client.post(reverse('sales:pos'), {
            'cart_data': json.dumps([{'id': product.id, 'qty': 1, 'weight': 1}]),
            'payment_method': str(cash.id), 'price_list': 'default',
        }, headers={'X-POS-Ajax': '1'})

        response = self.client.get(reverse('products:bi_dashboard'), {'period': 'today'})

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Decimal(")
        hours = json.loads(response.context['sales_hour_data'])
        self.assertEqual(sum(h['total'] for h in hours), 3000.0)
        self.assertIn('\\u003cb>', response.context['top_products'])  # nombre escapado dentro del <script>
        self.assertEqual(response.context['net_profit_data']['revenue'], Decimal('3000.00'))

    def test_supplier_ranking_counts_purchases(self):
        """Antes buscaba movimientos 'IN' y las compras se registran como 'PURCHASE_IN'."""
        supplier = Supplier.objects.create(name='Avícola')
        product = Product.objects.create(sku='P', name='Pollo', unit='kg', price=Decimal('4000'), supplier=supplier)
        self.client.post(reverse('products:purchase_create'), {
            'supplier_id': supplier.id, 'invoice_number': 'F1',
            'items_data': json.dumps([{'product_id': product.id, 'qty': '10', 'cost': '2000'}]),
        })

        ranking = list(self.client.get(reverse('products:bi_dashboard'), {'period': 'month'}).context['supplier_ranking'])

        self.assertEqual(ranking[0]['product__supplier__name'], 'Avícola')
        self.assertEqual(ranking[0]['total_spent'], Decimal('20000'))


class BulkPriceUpdateBugTests(TestCase):
    def setUp(self):
        self.user = _admin('admin_precios')
        self.client.force_login(self.user)
        self.category = Category.objects.create(name='Pollería')
        self.product = Product.objects.create(sku='X', name='Pechuga', price=Decimal('1000.00'), category=self.category)

    def test_comma_percentage_rounds_to_cents_and_logs_history(self):
        self.client.post(reverse('products:admin_tools'), {'category': self.category.id, 'percentage': '10,55'})

        self.product.refresh_from_db()
        self.assertEqual(self.product.price, Decimal('1105.50'))
        self.assertTrue(PriceLog.objects.filter(product=self.product, new_price=Decimal('1105.50')).exists())

    def test_invalid_input_does_not_crash(self):
        """Antes '10,5', un texto o una categoría inexistente daban error 500."""
        for data in ({'percentage': 'abc'}, {'percentage': ''}, {'category': '999', 'percentage': '5'}, {'category': 'x', 'percentage': '5'}):
            response = self.client.post(reverse('products:admin_tools'), data)
            self.assertEqual(response.status_code, 302, data)
        self.product.refresh_from_db()
        self.assertEqual(self.product.price, Decimal('1000.00'))


class EscapingAndRolesBugTests(TestCase):
    def test_pos_promotions_json_cannot_close_script_tag(self):
        user = _admin('cajero_xss')
        self.client.force_login(user)
        CashSession.objects.create(user=user, initial_amount=Decimal('0'))
        today = timezone.localdate()
        Promotion.objects.create(name='</script><script>alert(1)</script>', promo_type='PERCENT',
                                 discount_percentage=Decimal('10'), start_date=today, end_date=today)

        response = self.client.get(reverse('sales:pos'))

        self.assertNotContains(response, '</script><script>alert(1)')
        self.assertContains(response, 'function escapeHtml')

    def test_admin_menu_for_administradores_group_and_multiple_groups(self):
        """Antes el menú solo miraba el primer grupo y solo el nombre 'Administrador'."""
        user = User.objects.create_user(username='multi', password='pass12345')
        user.groups.add(Group.objects.get_or_create(name='Cajero')[0], Group.objects.get_or_create(name='Administradores')[0])
        self.client.force_login(user)

        response = self.client.get(reverse('dashboard'))

        self.assertTrue(response.context['es_admin'])
        self.assertContains(response, reverse('configuracion'))


class PriceChangeAuthorityTests(TestCase):
    """Los precios los cambian solo el superadministrador y el dueño; el historial guarda quién."""

    def setUp(self):
        self.owner = _admin('duenio_precios')
        self.manager = User.objects.create_user(username='encargado_precios', password='pass12345', is_staff=True)
        self.manager.groups.add(Group.objects.get_or_create(name='Encargados')[0])
        self.product = Product.objects.create(sku='PEC', name='Pechuga', unit='kg', price=Decimal('5000.00'),
                                              cost_price=Decimal('3000.00'), stock=Decimal('5'))

    def _edit(self, user, price, cost):
        self.client.force_login(user)
        return self.client.post(reverse('products:product_update', args=[self.product.id]), {
            'sku': 'PEC', 'name': 'Pechuga', 'unit': 'kg', 'price': price, 'cost_price': cost,
            'tax_rate': '21', 'stock': '5', 'min_stock': '5',
        })

    def test_owner_change_is_logged_with_user_and_reason(self):
        self._edit(self.owner, '5500.00', '3100.00')

        self.product.refresh_from_db()
        self.assertEqual((self.product.price, self.product.cost_price), (Decimal('5500.00'), Decimal('3100.00')))
        sale_log = PriceLog.objects.get(product=self.product, price_type='SALE')
        self.assertEqual((sale_log.user, sale_log.reason, sale_log.old_price), (self.owner, 'Edición del producto', Decimal('5000.00')))
        cost_log = PriceLog.objects.get(product=self.product, price_type='COST')
        self.assertEqual((cost_log.user, cost_log.new_price), (self.owner, Decimal('3100.00')))

    def test_manager_cannot_change_prices_from_the_form(self):
        page = self._edit(self.manager, '1.00', '1.00')

        self.product.refresh_from_db()
        self.assertEqual((self.product.price, self.product.cost_price), (Decimal('5000.00'), Decimal('3000.00')))
        self.assertFalse(PriceLog.objects.exists())
        self.client.force_login(self.manager)
        form = self.client.get(reverse('products:product_update', args=[self.product.id])).context['form']
        self.assertTrue(form.fields['price'].disabled)

    def test_any_other_path_is_blocked_for_non_owners(self):
        """Última barrera: aunque una pantalla no lo controle, el guardado rechaza el cambio."""
        from django.core.exceptions import PermissionDenied
        from products.signals import set_current_user
        set_current_user(self.manager)
        try:
            self.product.price = Decimal('1.00')
            with self.assertRaises(PermissionDenied):
                self.product.save()
        finally:
            set_current_user(None)

    def test_import_by_non_owner_keeps_prices_and_warns(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        self.client.force_login(self.manager)
        upload = SimpleUploadedFile('p.csv', b"sku,nombre,precio,stock\nPEC,Pechuga,1,8\n", content_type='text/csv')

        response = self.client.post(reverse('products:import_inventory_excel'), {'excel_file': upload}, follow=True)

        self.product.refresh_from_db()
        self.assertEqual(self.product.price, Decimal('5000.00'))
        self.assertEqual(self.product.stock, Decimal('8'))  # el resto de los datos sí se actualiza
        self.assertTrue(any('sin cambio de precio' in str(m) for m in response.context['messages']))

    def test_kardex_shows_price_history(self):
        self._edit(self.owner, '5500.00', '3000.00')

        response = self.client.get(reverse('products:product_kardex', args=[self.product.id]))

        self.assertContains(response, 'Historial de precios')
        self.assertContains(response, 'duenio_precios')
        self.assertContains(response, 'Edición del producto')


class WhatsappPhoneTests(TestCase):
    def test_argentine_numbers_are_normalized_for_wa_me(self):
        from customers.phones import whatsapp_number
        cases = {
            '+54 9 11 5555-1234': '5491155551234',
            '11 5555-1234': '5491155551234',
            '011 15 5555-1234': '5491155551234',
            '0351 15 123-4567': '5493511234567',
            '(0294) 15-412-3456': '5492944123456',
            '541155551234': '5491155551234',
            '15 5555-1234': '',  # falta el código de área
            '123': '',
            None: '',
        }
        for raw, expected in cases.items():
            self.assertEqual(whatsapp_number(raw), expected, raw)

    def test_ticket_link_uses_normalized_number(self):
        user = User.objects.create_superuser(username='wa_root', password='pass12345')
        self.client.force_login(user)
        cash = PaymentMethod.objects.create(name='Efectivo')
        customer = Customer.objects.create(full_name='Rosa', dni_cuit='27000111', phone='011 15 5555-1234')
        sale = Sale.objects.create(user=user, customer=customer, total_amount=Decimal('100'), payment_method=cash)

        response = self.client.get(reverse('sales:whatsapp_ticket', args=[sale.id]))

        self.assertTrue(response['Location'].startswith('https://wa.me/5491155551234?text='))


class ProductDeactivationTests(TestCase):
    """Un producto con historial no se borra (se perderían ventas, compras y mermas): se desactiva."""

    def setUp(self):
        self.root = User.objects.create_superuser(username='root_desact', password='pass12345')
        self.client.force_login(self.root)
        self.cash = PaymentMethod.objects.create(name='Efectivo')
        CashSession.objects.create(user=self.root, initial_amount=Decimal('0'))
        self.sold = Product.objects.create(sku='VEN', name='Vendido', unit='kg', price=Decimal('1000'),
                                           stock=Decimal('5'), barcode='7790000000017', plu=12)
        self.client.post(reverse('sales:pos'), {
            'cart_data': json.dumps([{'id': self.sold.id, 'qty': 1, 'weight': 1}]),
            'payment_method': str(self.cash.id), 'price_list': 'default',
        }, headers={'X-POS-Ajax': '1'})
        self.unused = Product.objects.create(sku='NUE', name='Nunca usado', price=Decimal('10'))

    def test_product_without_history_is_deleted(self):
        self.client.post(reverse('products:product_delete', args=[self.unused.id]))
        self.assertFalse(Product.objects.filter(pk=self.unused.pk).exists())

    def test_product_with_history_is_deactivated_and_keeps_records(self):
        page = self.client.get(reverse('products:product_delete', args=[self.sold.id]))
        self.assertContains(page, '¿Desactivar producto?')

        self.client.post(reverse('products:product_delete', args=[self.sold.id]))

        self.sold.refresh_from_db()
        self.assertFalse(self.sold.active)
        self.assertEqual(Sale.objects.count(), 1)
        self.assertTrue(InventoryMovement.objects.filter(product=self.sold).exists())

    def test_deactivated_product_is_not_offered_for_operations(self):
        from products.barcodes import resolve_code
        self.client.post(reverse('products:product_delete', args=[self.sold.id]))

        self.assertNotContains(self.client.get(reverse('sales:pos')), 'data-id="%d"' % self.sold.id)
        catalog = self.client.get(reverse('sales:pos_catalog')).json()['products']
        self.assertNotIn(self.sold.id, [p['id'] for p in catalog])
        self.assertIsNone(resolve_code('7790000000017'))
        self.assertNotIn(self.sold, self.client.get(reverse('products:product_list')).context['products'])
        quick = self.client.post(reverse('products:product_quick_create'), json.dumps({
            'barcode': '7790000000017', 'name': 'Otro', 'unit': 'unidad', 'price': '1'}), content_type='application/json')
        self.assertIn('desactivado', quick.json()['error'])

    def test_reactivate_from_inactive_list(self):
        self.client.post(reverse('products:product_delete', args=[self.sold.id]))
        listing = self.client.get(reverse('products:product_list'), {'inactivos': '1'})
        self.assertIn(self.sold, listing.context['products'])
        self.assertContains(listing, f'id="reactivate-{self.sold.id}"')

        self.client.post(reverse('products:product_reactivate', args=[self.sold.id]))

        self.sold.refresh_from_db()
        self.assertTrue(self.sold.active)

    def test_offline_sale_of_deactivated_product_still_syncs(self):
        """Una venta hecha sin conexión antes de desactivar el producto se sincroniza igual."""
        self.client.post(reverse('products:product_delete', args=[self.sold.id]))
        result = self.client.post(reverse('sales_sync'), json.dumps({'sales': [{
            'client_uuid': '5f1d3c2a-8e4b-4a6f-9c3d-2b1a0f9e8d7c',
            'items': [{'id': self.sold.id, 'qty': 1, 'weight': 0.5}],
            'payment_method_id': str(self.cash.id), 'price_list': 'default',
        }]}), content_type='application/json').json()['results']
        self.assertEqual(result[0]['status'], 'created')
