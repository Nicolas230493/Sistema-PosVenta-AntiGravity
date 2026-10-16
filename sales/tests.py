import json
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth.models import User, Permission
from django.contrib.contenttypes.models import ContentType
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from customers.models import Customer, CurrentAccount
from finance.models import CashSession, PaymentMethod
from products.models import Category, InventoryMovement, Product
from suppliers.models import Supplier
from core.models import TurnoCaja
from sales.models import Sale, SaleDetail

class POSTestCase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='cashier', password='pass12345')
        add_sale_perm = Permission.objects.get(codename='add_sale', content_type=ContentType.objects.get(app_label='sales', model='sale'))
        self.user.user_permissions.add(add_sale_perm)
        self.client.force_login(self.user)
        # Crear turno de caja
        TurnoCaja.objects.create(usuario=self.user, monto_inicial=Decimal('100.00'), estado='ABIERTO')
        self.supplier = Supplier.objects.create(name='Proveedor')
        self.category = Category.objects.create(name='Almacen')
        self.customer = Customer.objects.create(
            full_name='Cliente Test',
            dni_cuit='12345678',
            limite_credito=Decimal('1000.00'),
            points=100,
        )
        self.default_customer, _ = Customer.objects.get_or_create(
            dni_cuit='00000000',
            defaults={
                'full_name': 'Consumidor Final',
            },
        )
        self.cash = PaymentMethod.objects.create(name='Efectivo')
        self.current_account = PaymentMethod.objects.create(name='Cuenta Corriente')
        self.product = Product.objects.create(
            sku='SKU123',
            barcode='7791234567890',
            name='Yerba',
            category=self.category,
            price=Decimal('100.00'),
            cost_price=Decimal('60.00'),
            stock=5,
            min_stock=1,
            supplier=self.supplier,
        )
        CashSession.objects.create(user=self.user, initial_amount=Decimal('100.00'))

    def _post_sale(self, cart, **overrides):
        data = {
            'cart_data': json.dumps(cart),
            'customer_id': str(self.customer.id),
            'payment_method': str(self.cash.id),
            'discount_amount': '0',
            'surcharge_amount': '0',
            'points_redeemed': '0',
            'price_list': 'default',
        }
        data.update(overrides)
        return self.client.post(reverse('sales:pos'), data)

    def test_sale_uses_server_price_instead_of_tampered_cart_price(self):
        cart = [{
            'id': self.product.id,
            'name': self.product.name,
            'qty': 2,
            'price': '0.01',
            'discounted_price': '0.01',
            'original_price_at_sale': '0.01',
        }]

        response = self._post_sale(cart)

        self.assertEqual(response.status_code, 302)
        sale = Sale.objects.get()
        detail = SaleDetail.objects.get(sale=sale)
        self.assertEqual(sale.total_amount, Decimal('200.00'))
        self.assertEqual(detail.price, Decimal('100.00'))
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, 3)

    def test_sale_rejects_negative_quantity(self):
        cart = [{
            'id': self.product.id,
            'name': self.product.name,
            'qty': -1,
            'price': '100.00',
            'discounted_price': '100.00',
            'original_price_at_sale': '100.00',
        }]

        self._post_sale(cart)

        self.assertEqual(Sale.objects.count(), 0)
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, 5)

    def test_current_account_sale_updates_balance_and_single_ledger_entry(self):
        cart = [{
            'id': self.product.id,
            'name': self.product.name,
            'qty': 1,
            'price': '100.00',
            'discounted_price': '100.00',
            'original_price_at_sale': '100.00',
        }]

        self._post_sale(cart, payment_method=str(self.current_account.id))

        self.customer.refresh_from_db()
        self.assertEqual(self.customer.balance, Decimal('100.00'))
        self.assertEqual(CurrentAccount.objects.filter(customer=self.customer, entry_type='DEBT').count(), 1)

    def test_stock_entry_scanner_finds_barcode_and_rejects_negative_quantity(self):
        self.client.post(reverse('products:stock_entry_scanner'), {
            'barcode': self.product.barcode,
            'quantity': '3',
        })
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, 8)
        self.assertTrue(InventoryMovement.objects.filter(reference='Ingreso por Escáner', quantity=3).exists())

        self.client.post(reverse('products:stock_entry_scanner'), {
            'barcode': self.product.barcode,
            'quantity': '-10',
        })
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, 8)

    def test_sale_pdf_and_thermal_ticket_exports_return_pdf(self):
        sale = Sale.objects.create(
            user=self.user,
            customer=self.customer,
            total_amount=Decimal('100.00'),
            tax_amount=Decimal('17.36'),
            payment_method=self.cash,
        )
        SaleDetail.objects.create(
            sale=sale,
            product=self.product,
            quantity=1,
            price=Decimal('100.00'),
            cost_price_at_sale=Decimal('60.00'),
            tax_rate=Decimal('21.00'),
            tax_amount=Decimal('17.36'),
            subtotal=Decimal('100.00'),
        )

        pdf_response = self.client.get(reverse('sales:export_sale_pdf', args=[sale.id]))
        ticket_response = self.client.get(reverse('sales:export_thermal_ticket', args=[sale.id]))

        self.assertEqual(pdf_response.status_code, 200)
        self.assertEqual(pdf_response['Content-Type'], 'application/pdf')
        self.assertTrue(bytes(pdf_response.content).startswith(b'%PDF'))
        self.assertEqual(ticket_response.status_code, 200)
        self.assertEqual(ticket_response['Content-Type'], 'application/pdf')


class DynamicStockTests(TestCase):
    """SPEC-INV-02 §3.2: la venta nunca se bloquea por stock."""

    def setUp(self):
        self.user = User.objects.create_user(username='cajero2', password='pass12345')
        self.user.user_permissions.add(Permission.objects.get(codename='add_sale', content_type__app_label='sales'))
        self.client.force_login(self.user)
        self.cash = PaymentMethod.objects.create(name='Efectivo')
        self.session = CashSession.objects.create(user=self.user, initial_amount=Decimal('0'))
        self.pollo = Product.objects.create(
            sku='POLLO1', name='Pata Muslo', unit='kg', price=Decimal('4000.00'),
            cost_price=Decimal('2500.00'), stock=Decimal('1.000'), min_stock=Decimal('0'),
        )
        self.pan = Product.objects.create(
            sku='PAN1', name='Pan Francés', price=Decimal('100.00'), stock=Decimal('5'), min_stock=Decimal('0'),
        )

    def _sell(self, items, headers=None, **extra):
        data = {'cart_data': json.dumps(items), 'payment_method': str(self.cash.id), 'price_list': 'default'}
        data.update(extra)
        return self.client.post(reverse('sales:pos'), data, headers=headers or {})

    def test_case_a_regular_discount(self):
        self._sell([{'id': self.pan.id, 'qty': 2}])

        self.pan.refresh_from_db()
        self.assertEqual(self.pan.stock, Decimal('3'))
        self.assertFalse(InventoryMovement.objects.filter(movement_type='AUTO_ADJUST_IN').exists())
        self.assertTrue(InventoryMovement.objects.filter(product=self.pan, movement_type='OUT', quantity=2).exists())

    def test_case_b_oversold_creates_auto_adjust_and_zeroes_stock(self):
        self._sell([{'id': self.pollo.id, 'qty': 1, 'weight': '2.350'}])

        sale = Sale.objects.get()
        self.pollo.refresh_from_db()
        self.assertEqual(self.pollo.stock, Decimal('0.000'))
        adjust = InventoryMovement.objects.get(movement_type='AUTO_ADJUST_IN')
        self.assertEqual(adjust.quantity, Decimal('1.350'))
        self.assertEqual(adjust.reason, f"Auto-ajuste dinámico en POS por venta #{sale.id} (Stock previo: 1.000)")
        self.assertEqual(sale.cash_session, self.session)

    def test_sale_allowed_with_zero_stock(self):
        self.pan.stock = Decimal('0')
        self.pan.save()

        self._sell([{'id': self.pan.id, 'qty': 4}])

        self.assertEqual(Sale.objects.count(), 1)
        self.pan.refresh_from_db()
        self.assertEqual(self.pan.stock, Decimal('0'))
        self.assertEqual(InventoryMovement.objects.get(movement_type='AUTO_ADJUST_IN').quantity, Decimal('4'))

    def test_fractional_weight_is_charged_and_discounted(self):
        self.pollo.stock = Decimal('10')
        self.pollo.save()

        self._sell([{'id': self.pollo.id, 'qty': 1, 'weight': '0.350'}])

        detail = SaleDetail.objects.get()
        self.assertEqual(detail.quantity, Decimal('0.350'))
        self.assertEqual(detail.subtotal, Decimal('1400.00'))
        self.pollo.refresh_from_db()
        self.assertEqual(self.pollo.stock, Decimal('9.650'))

    def test_pos_ajax_post_returns_json(self):
        response = self._sell([{'id': self.pan.id, 'qty': 1}], headers={'X-POS-Ajax': '1'})

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['ok'])

    def test_same_client_uuid_is_not_duplicated(self):
        uid = '6f1c1e2a-3b4c-4d5e-8f90-112233445566'
        self._sell([{'id': self.pan.id, 'qty': 1}], client_uuid=uid)
        self._sell([{'id': self.pan.id, 'qty': 1}], client_uuid=uid)

        self.assertEqual(Sale.objects.count(), 1)
        self.pan.refresh_from_db()
        self.assertEqual(self.pan.stock, Decimal('4'))


class CashSessionDetectionTests(TestCase):
    """SPEC-POS-03 §4.2: el turno activo se detecta solo por estado."""

    def setUp(self):
        self.user = User.objects.create_user(username='cajero3', password='pass12345')
        self.user.user_permissions.add(Permission.objects.get(codename='add_sale', content_type__app_label='sales'))
        self.client.force_login(self.user)

    def test_pos_without_open_session_shows_open_form(self):
        response = self.client.get(reverse('sales:pos'))

        self.assertTemplateUsed(response, 'sales/pos_no_session.html')

    def test_session_from_previous_day_opened_by_other_user_allows_selling(self):
        other = User.objects.create_user(username='manana', password='pass12345')
        session = CashSession.objects.create(user=other, initial_amount=Decimal('0'))
        CashSession.objects.filter(pk=session.pk).update(start_date=timezone.now() - timedelta(days=1))

        response = self.client.get(reverse('sales:pos'))

        self.assertTemplateUsed(response, 'sales/pos.html')
        self.assertTrue(response.context['session_from_previous_day'])

    def test_open_cash_from_pos_redirects_back(self):
        response = self.client.post(reverse('finance:open_cash'), {'amount': '500', 'next': reverse('sales:pos')})

        self.assertRedirects(response, reverse('sales:pos'), fetch_redirect_response=False)
        self.assertTrue(CashSession.objects.filter(is_open=True).exists())

    def test_open_cash_does_not_create_second_open_session(self):
        CashSession.objects.create(user=self.user, initial_amount=Decimal('0'))

        self.client.post(reverse('finance:open_cash'), {'amount': '100'})

        self.assertEqual(CashSession.objects.filter(is_open=True).count(), 1)


class OfflineSyncTests(TestCase):
    """SPEC-SYNC-05 §6.2: sincronización idempotente por client_uuid."""

    def setUp(self):
        self.user = User.objects.create_user(username='cajero4', password='pass12345')
        self.user.user_permissions.add(Permission.objects.get(codename='add_sale', content_type__app_label='sales'))
        self.client.force_login(self.user)
        self.cash = PaymentMethod.objects.create(name='Efectivo')
        self.session = CashSession.objects.create(user=self.user, initial_amount=Decimal('0'))
        self.product = Product.objects.create(sku='ALITA', name='Alitas', unit='kg', price=Decimal('3000.00'), stock=Decimal('0'))

    def _sync(self, sales):
        return self.client.post(reverse('sales_sync'), json.dumps({'sales': sales}), content_type='application/json')

    def _offline_sale(self, uid, weight='1.500', created_at=None):
        return {
            'client_uuid': uid,
            'created_at': created_at or timezone.now().isoformat(),
            'cart': [{'id': self.product.id, 'qty': 1, 'weight': weight}],
            'payment_method': str(self.cash.id),
            'price_list': 'default',
        }

    def test_sync_creates_sales_and_ignores_duplicates(self):
        batch = [
            self._offline_sale('11111111-1111-4111-8111-111111111111'),
            self._offline_sale('22222222-2222-4222-8222-222222222222', weight='0.500'),
        ]

        first = self._sync(batch).json()['results']
        second = self._sync(batch).json()['results']

        self.assertEqual([r['status'] for r in first], ['created', 'created'])
        self.assertEqual([r['status'] for r in second], ['duplicate', 'duplicate'])
        self.assertEqual(Sale.objects.count(), 2)
        self.assertTrue(all(s.cash_session_id == self.session.id for s in Sale.objects.all()))
        # Stock dinámico aplicado: 2 kg vendidos sin stock -> 2 kg de auto-ajuste
        total_adjust = sum(m.quantity for m in InventoryMovement.objects.filter(movement_type='AUTO_ADJUST_IN'))
        self.assertEqual(total_adjust, Decimal('2.000'))

    def test_sync_keeps_local_sale_time(self):
        created_at = timezone.now() - timedelta(hours=2)
        self._sync([self._offline_sale('33333333-3333-4333-8333-333333333333', created_at=created_at.isoformat())])

        self.assertEqual(Sale.objects.get().fecha_hora.replace(microsecond=0), created_at.replace(microsecond=0))

    def test_sync_reports_row_errors_without_failing_batch(self):
        bad = self._offline_sale('44444444-4444-4444-8444-444444444444')
        bad['cart'] = [{'id': 999999, 'qty': 1}]
        good = self._offline_sale('55555555-5555-4555-8555-555555555555')

        results = self._sync([bad, good]).json()['results']

        self.assertEqual(results[0]['status'], 'error')
        self.assertEqual(results[1]['status'], 'created')

    def test_sync_rejects_invalid_json_and_anonymous(self):
        response = self.client.post(reverse('sales_sync'), 'no-json', content_type='application/json')
        self.assertEqual(response.status_code, 400)

        self.client.logout()
        response = self._sync([])
        self.assertEqual(response.status_code, 302)

    def test_catalog_and_service_worker_endpoints(self):
        catalog = self.client.get(reverse('sales:pos_catalog')).json()
        self.assertEqual(catalog['products'][0]['name'], 'Alitas')

        sw = self.client.get(reverse('sales:service_worker'))
        self.assertEqual(sw['Content-Type'], 'application/javascript')
