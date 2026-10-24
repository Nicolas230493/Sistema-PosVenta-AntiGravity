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
            unit_cost=Decimal('60.00'),
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

    def test_service_worker_and_manifest_served_from_root(self):
        """SPEC-PWA-11 §11.1: /sw.js y /manifest.json en la raíz con Service-Worker-Allowed: /."""
        self.client.logout()  # deben responder aunque la sesión haya expirado

        sw = self.client.get('/sw.js')
        self.assertEqual(sw.status_code, 200)
        self.assertEqual(sw['Content-Type'], 'application/javascript')
        self.assertEqual(sw['Service-Worker-Allowed'], '/')
        self.assertIn(b'/pos/api/catalog/', sw.content)

        manifest = self.client.get('/manifest.json')
        self.assertEqual(manifest.status_code, 200)
        self.assertEqual(manifest['Service-Worker-Allowed'], '/')
        self.assertIn('application/manifest+json', manifest['Content-Type'])
        data = json.loads(manifest.content)
        self.assertEqual(data['start_url'], reverse('sales:pos'))
        self.assertEqual(data['scope'], '/')
        self.assertTrue(data['icons'])

    def test_catalog_returns_current_csrf_token(self):
        """El POS abierto desde la caché renueva su token CSRF con el catálogo."""
        response = self.client.get(reverse('sales:pos_catalog'))

        self.assertTrue(response.json()['csrf_token'])
        self.assertEqual(response['Cache-Control'], 'no-store')

    def test_service_worker_dev_mode_follows_debug(self):
        from django.test import override_settings
        self.assertIn(b'const DEV_MODE = false;', self.client.get('/sw.js').content)
        with override_settings(DEBUG=True):
            self.assertIn(b'const DEV_MODE = true;', self.client.get('/sw.js').content)

    def test_pos_page_wires_root_service_worker_and_status_badge(self):
        """SPEC-PWA-11 §11.2: el POS registra /sw.js y muestra el semáforo en la barra superior."""
        response = self.client.get(reverse('sales:pos'))

        self.assertContains(response, 'swUrl: "/sw.js"')
        self.assertContains(response, 'id="pos-net-badge"')
        self.assertContains(response, 'id="pos-sync-now"')
        self.assertContains(response, 'rel="manifest" href="/manifest.json"')

    def test_sync_accepts_items_key_from_indexeddb_queue(self):
        """SPEC-PWA-11 §11.2: la cola de IndexedDB guarda 'items' (antes 'cart')."""
        sale = self._offline_sale('44444444-4444-4444-8444-444444444444')
        sale['items'] = sale.pop('cart')
        sale['total_amount'] = '4500.00'

        results = self._sync([sale]).json()['results']

        self.assertEqual(results[0]['status'], 'created')
        self.assertEqual(Sale.objects.get().total_amount, Decimal('4500.00'))

    def test_sale_cancellation_reverts_stock_with_sale_cancel_in(self):
        from sales.services import cancel_sale
        # Venta de producto con stock inicial
        prod = Product.objects.create(
            sku='POLLO_CANC',
            name='Pollo Anulacion',
            unit='kg',
            price=Decimal('2000.00'),
            cost_price=Decimal('1200.00'),
            stock=Decimal('10.000'),
        )
        cart = [{'id': prod.id, 'qty': 1, 'weight': '3.000'}]
        # Registrar venta por POS
        sale_res = self.client.post(reverse('sales:pos'), {
            'cart_data': json.dumps(cart),
            'payment_method': str(self.cash.id),
        }, follow=True)
        self.assertEqual(sale_res.status_code, 200)

        prod.refresh_from_db()
        self.assertEqual(prod.stock, Decimal('7.000')) # 10 - 3

        sale = Sale.objects.order_by('-id').first()
        self.assertFalse(sale.is_cancelled)

        # Hacer usuario staff/superuser para pasar manager_required
        self.user.is_superuser = True
        self.user.save()

        # Anular la venta
        cancel_res = self.client.post(reverse('sales:sale_cancel', kwargs={'pk': sale.pk}), {
            'reason': 'Error de carga en mostrador',
        }, follow=True)
        self.assertEqual(cancel_res.status_code, 200)

        sale.refresh_from_db()
        self.assertTrue(sale.is_cancelled)
        self.assertEqual(sale.cancel_reason, 'Error de carga en mostrador')

        # El stock debe haberse revertido exactamente
        prod.refresh_from_db()
        self.assertEqual(prod.stock, Decimal('10.000'))

        # Se debe haber generado un movimiento SALE_CANCEL_IN
        cancel_mov = InventoryMovement.objects.filter(product=prod, movement_type='SALE_CANCEL_IN').first()
        self.assertIsNotNone(cancel_mov)
        self.assertEqual(cancel_mov.quantity, Decimal('3.000'))
        self.assertEqual(cancel_mov.type, 'SALE_CANCEL_IN')

    def test_discrete_units_reject_fractional_sale_in_pos(self):
        prod = Product.objects.create(
            sku='MAPLE_HUEVOS',
            name='Maple de Huevos',
            unit='unidad',
            price=Decimal('3000.00'),
            stock=Decimal('10.000'),
        )
        cart = [{'id': prod.id, 'qty': '1.5'}]
        response = self.client.post(reverse('sales:pos'), {
            'cart_data': json.dumps(cart),
            'payment_method': str(self.cash.id),
        }, follow=True)
        self.assertContains(response, 'no admite decimales')
        prod.refresh_from_db()
        self.assertEqual(prod.stock, Decimal('10.000')) # Intacto


class MixedPaymentAndReservationTests(TestCase):
    """SPEC-POS-08: pagos mixtos, señas/apartados y ticket térmico. SPEC-FIN-06 §6.2: costo congelado."""

    def setUp(self):
        from products.models import PriceList, ProductPrice
        self.user = User.objects.create_user(username='cajero_mix', password='pass12345')
        self.user.user_permissions.add(Permission.objects.get(codename='add_sale', content_type__app_label='sales'))
        self.client.force_login(self.user)
        self.cash = PaymentMethod.objects.create(name='Efectivo')
        self.transfer = PaymentMethod.objects.create(name='Transferencia', is_digital=True)
        self.current_account = PaymentMethod.objects.create(name='Cuenta Corriente')
        self.session = CashSession.objects.create(user=self.user, initial_amount=Decimal('0'))
        self.customer = Customer.objects.create(full_name='Cliente Mix', dni_cuit='30111222')
        self.product = Product.objects.create(
            sku='PECHU', name='Pechuga', unit='kg', price=Decimal('5000.00'),
            cost_price=Decimal('3000.00'), stock=Decimal('10.000'),
        )
        self.mayorista = PriceList.objects.create(name='Mayorista')
        ProductPrice.objects.create(product=self.product, price_list=self.mayorista, price=Decimal('4000.00'))

    def _post(self, payments=None, status='COMPLETED', weight='1.000', **extra):
        data = {
            'cart_data': json.dumps([{'id': self.product.id, 'qty': 1, 'weight': weight}]),
            'customer_id': str(self.customer.id),
            'payment_method': str(self.cash.id),
            'price_list': 'default',
            'sale_status': status,
        }
        if payments is not None:
            data['payments_data'] = json.dumps(payments)
        data.update(extra)
        return self.client.post(reverse('sales:pos'), data, headers={'X-POS-Ajax': '1'})

    def test_mixed_payment_creates_one_sale_payment_per_method(self):
        response = self._post([
            {'method_id': self.cash.id, 'amount': '3000'},
            {'method_id': self.transfer.id, 'amount': '2000', 'reference': 'OP-123'},
        ])

        self.assertEqual(response.status_code, 200, response.content)
        sale = Sale.objects.get()
        self.assertEqual(sale.total_amount, Decimal('5000.00'))
        self.assertEqual(
            sorted((p.payment_method.name, p.amount, p.reference) for p in sale.payments.all()),
            [('Efectivo', Decimal('3000.00'), ''), ('Transferencia', Decimal('2000.00'), 'OP-123')],
        )
        self.assertTrue(all(p.cash_session == self.session for p in sale.payments.all()))
        self.assertEqual(sale.payment_method, self.cash)

    def test_payments_must_match_total_exactly(self):
        response = self._post([
            {'method_id': self.cash.id, 'amount': '3000'},
            {'method_id': self.transfer.id, 'amount': '1999.99'},
        ])

        self.assertEqual(response.status_code, 400)
        self.assertIn('debe ser igual al total', response.json()['error'])
        self.assertFalse(Sale.objects.exists())
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, Decimal('10.000'))

    def test_single_payment_method_creates_single_sale_payment(self):
        self._post()

        payment = Sale.objects.get().payments.get()
        self.assertEqual(payment.payment_method, self.cash)
        self.assertEqual(payment.amount, Decimal('5000.00'))

    def test_current_account_portion_debits_only_that_amount(self):
        self._post([
            {'method_id': self.cash.id, 'amount': '1000'},
            {'method_id': self.current_account.id, 'amount': '4000'},
        ])

        self.customer.refresh_from_db()
        self.assertEqual(self.customer.balance, Decimal('4000.00'))
        ledger = CurrentAccount.objects.get(customer=self.customer)
        self.assertEqual((ledger.entry_type, ledger.amount), ('DEBT', Decimal('4000.00')))

    def test_cancelling_mixed_sale_reverts_current_account_portion(self):
        from sales.services import cancel_sale
        self._post([
            {'method_id': self.cash.id, 'amount': '1000'},
            {'method_id': self.current_account.id, 'amount': '4000'},
        ])
        sale = Sale.objects.get()

        cancel_sale(sale=sale, user=self.user, reason='Error de carga')

        self.customer.refresh_from_db()
        sale.refresh_from_db()
        self.assertEqual(self.customer.balance, Decimal('0.00'))
        self.assertEqual(sale.status, Sale.STATUS_CANCELLED)
        self.assertTrue(sale.is_cancelled)

    def test_sale_detail_freezes_unit_cost(self):
        self._post()
        self.product.cost_price = Decimal('9999.00')
        self.product.save()

        detail = SaleDetail.objects.get()
        self.assertEqual(detail.unit_cost, Decimal('3000.00'))

    def test_reservation_with_deposit_then_completion(self):
        response = self._post([{'method_id': self.cash.id, 'amount': '1500'}], status='RESERVED', weight='2.000')

        self.assertEqual(response.status_code, 200, response.content)
        sale = Sale.objects.get()
        self.assertEqual(sale.status, Sale.STATUS_RESERVED)
        self.assertEqual(sale.amount_paid, Decimal('1500.00'))
        self.assertEqual(sale.balance_due, Decimal('8500.00'))
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, Decimal('8.000'))  # el stock queda comprometido

        # Pago incompleto: rechazado
        self.client.post(reverse('sales:reservation_complete', args=[sale.id]), {
            'payment_method': self.transfer.id, 'amount': '100',
        })
        sale.refresh_from_db()
        self.assertEqual(sale.status, Sale.STATUS_RESERVED)

        # El saldo se cobra en el turno abierto actual
        self.session.is_open = False
        self.session.save()
        new_session = CashSession.objects.create(user=self.user, initial_amount=Decimal('0'))
        self.client.post(reverse('sales:reservation_complete', args=[sale.id]), {
            'payment_method': self.transfer.id, 'amount': '8500.00', 'reference': 'TR-9',
        })
        sale.refresh_from_db()
        self.assertEqual(sale.status, Sale.STATUS_COMPLETED)
        self.assertEqual(sale.balance_due, Decimal('0.00'))
        self.assertEqual(sale.payments.get(payment_method=self.transfer).cash_session, new_session)

    def test_reservation_without_deposit_is_allowed(self):
        response = self._post([], status='RESERVED', payment_method='')

        self.assertEqual(response.status_code, 200, response.content)
        sale = Sale.objects.get()
        self.assertEqual(sale.status, Sale.STATUS_RESERVED)
        self.assertFalse(sale.payments.exists())

    def test_reservation_deposit_cannot_exceed_total(self):
        response = self._post([{'method_id': self.cash.id, 'amount': '6000'}], status='RESERVED')

        self.assertEqual(response.status_code, 400)
        self.assertFalse(Sale.objects.exists())

    def test_sync_accepts_payments_array_and_single_method(self):
        base = {
            'cart': [{'id': self.product.id, 'qty': 1, 'weight': '1.000'}],
            'customer_id': self.customer.id,
            'price_list': 'default',
        }
        response = self.client.post(reverse('sales_sync'), json.dumps({'sales': [
            {**base, 'client_uuid': '8f1a3b8e-1111-4c4c-9a9a-000000000001',
             'payments': [{'method_id': self.cash.id, 'amount': 3000}, {'method_id': self.transfer.id, 'amount': 2000}]},
            {**base, 'client_uuid': '8f1a3b8e-1111-4c4c-9a9a-000000000002', 'payment_method_id': self.transfer.id},
        ]}), content_type='application/json')

        statuses = [r['status'] for r in response.json()['results']]
        self.assertEqual(statuses, ['created', 'created'])
        mixed = Sale.objects.get(client_uuid='8f1a3b8e-1111-4c4c-9a9a-000000000001')
        single = Sale.objects.get(client_uuid='8f1a3b8e-1111-4c4c-9a9a-000000000002')
        self.assertEqual(mixed.payments.count(), 2)
        self.assertEqual(single.payments.get().amount, Decimal('5000.00'))

    def test_customer_price_list_applies_when_none_selected(self):
        self.customer.price_list = self.mayorista
        self.customer.save()

        self._post(price_list='')

        self.assertEqual(Sale.objects.get().total_amount, Decimal('4000.00'))

    def test_escpos_ticket_renders_with_autoprint(self):
        self._post([
            {'method_id': self.cash.id, 'amount': '3000'},
            {'method_id': self.transfer.id, 'amount': '2000'},
        ])
        sale = Sale.objects.get()

        response = self.client.get(reverse('sales:ticket_escpos', args=[sale.id]), {'w': '58'})

        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn('58mm', content)
        self.assertIn('window.print()', content)
        self.assertIn('Transferencia', content)
        self.assertIn('@media print', content)

    def test_escpos_ticket_width_param_and_print_css(self):
        """SPEC-OPS-14 §14.2: @page 80mm por defecto, ?width=58, monoespaciada y .no-print."""
        self._post([{'method_id': self.cash.id, 'amount': '5000'}])
        url = reverse('sales:ticket_escpos', args=[Sale.objects.get().id])

        default = self.client.get(url).content.decode()
        narrow = self.client.get(url, {'width': '58'}).content.decode()

        self.assertIn('@page { margin: 0; size: 80mm auto; }', default)
        self.assertIn('@page { margin: 0; size: 58mm auto; }', narrow)
        self.assertIn('"Courier New", Courier, monospace', default)
        self.assertIn('.no-print { display: none !important; }', default)
        self.assertIn('class="toolbar no-print"', default)
        self.assertNotIn('<img', default)


class SaleReturnTests(TestCase):
    """La devolución reincorpora el stock una sola vez y revierte la parte fiada."""

    def setUp(self):
        self.user = User.objects.create_user(username='encargado_dev', password='pass12345', is_staff=True)
        self.user.user_permissions.add(Permission.objects.get(codename='add_sale', content_type__app_label='sales'))
        self.client.force_login(self.user)
        self.current_account = PaymentMethod.objects.create(name='Cuenta Corriente')
        CashSession.objects.create(user=self.user, initial_amount=Decimal('0'))
        self.customer = Customer.objects.create(full_name='Fiado', dni_cuit='20333444')
        self.product = Product.objects.create(sku='HUEVO', name='Maple', price=Decimal('100.00'), stock=Decimal('10'))

    def test_return_restores_stock_once_and_credits_current_account(self):
        self.client.post(reverse('sales:pos'), {
            'cart_data': json.dumps([{'id': self.product.id, 'qty': 3}]),
            'customer_id': str(self.customer.id),
            'payment_method': str(self.current_account.id),
            'price_list': 'default',
        })
        sale = Sale.objects.get()
        detail = sale.details.get()

        self.client.post(reverse('sales:sale_return', args=[sale.id]), {f'qty_{detail.id}': '2', 'reason': 'Rotos'})

        self.product.refresh_from_db()
        self.customer.refresh_from_db()
        self.assertEqual(self.product.stock, Decimal('9'))  # 10 - 3 + 2
        self.assertEqual(self.customer.balance, Decimal('100.00'))  # 300 fiado - 200 devuelto
        self.assertEqual(InventoryMovement.objects.filter(product=self.product, movement_type__in=['IN', 'SALE_CANCEL_IN']).count(), 1)


class ScaleLabelPosTests(TestCase):
    """Código de barras en el POS: etiquetas de balanza y cámara (agregado, sin cambiar el escáner)."""

    def setUp(self):
        self.user = User.objects.create_user(username='cajero_balanza', password='pass12345')
        self.user.user_permissions.add(Permission.objects.get(codename='add_sale', content_type__app_label='sales'))
        self.client.force_login(self.user)
        self.cash = PaymentMethod.objects.create(name='Efectivo')
        CashSession.objects.create(user=self.user, initial_amount=Decimal('0'))
        self.pechuga = Product.objects.create(sku='PEC', name='Pechuga', unit='kg', price=Decimal('5200.00'),
                                              stock=Decimal('10.000'), plu=42)

    def test_pos_page_exposes_plu_scale_config_and_camera(self):
        response = self.client.get(reverse('sales:pos'))

        self.assertContains(response, 'data-plu="42"')
        self.assertContains(response, 'POS_SCALE_CONFIG = { pluDigits: 5, mode: "peso" }')
        self.assertContains(response, 'id="camera-scan"')
        self.assertContains(response, 'js/barcode-tools.js')

    def test_scale_weight_with_three_decimals_sells_exactly(self):
        """Dos etiquetas (1,250 + 0,800 kg) quedan en una línea de 2,050 kg."""
        response = self.client.post(reverse('sales:pos'), {
            'cart_data': json.dumps([{'id': self.pechuga.id, 'qty': 1, 'weight': 2.05}]),
            'payment_method': str(self.cash.id),
            'price_list': 'default',
        }, headers={'X-POS-Ajax': '1'})

        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(Sale.objects.get().total_amount, Decimal('10660.00'))  # 2,050 kg × $5.200
        self.pechuga.refresh_from_db()
        self.assertEqual(self.pechuga.stock, Decimal('7.950'))

    def test_duplicate_lines_of_same_product_discount_all_stock(self):
        """Bug corregido: dos líneas del mismo producto descontaban solo la última del stock."""
        response = self.client.post(reverse('sales:pos'), {
            'cart_data': json.dumps([
                {'id': self.pechuga.id, 'qty': 1, 'weight': 1.25},
                {'id': str(self.pechuga.id), 'qty': 1, 'weight': 0.8},
            ]),
            'payment_method': str(self.cash.id),
            'price_list': 'default',
        }, headers={'X-POS-Ajax': '1'})

        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(Sale.objects.get().total_amount, Decimal('10660.00'))
        self.pechuga.refresh_from_db()
        self.assertEqual(self.pechuga.stock, Decimal('7.950'))

    def test_duplicate_lines_beyond_stock_create_single_correct_adjustment(self):
        """Con stock 1 kg y dos líneas de 0,8 kg: se descuenta 1 kg y se ajusta 0,6 kg (no 0)."""
        self.pechuga.stock = Decimal('1.000')
        self.pechuga.save()
        self.client.post(reverse('sales:pos'), {
            'cart_data': json.dumps([
                {'id': self.pechuga.id, 'qty': 1, 'weight': 0.8},
                {'id': self.pechuga.id, 'qty': 1, 'weight': 0.8},
            ]),
            'payment_method': str(self.cash.id),
            'price_list': 'default',
        }, headers={'X-POS-Ajax': '1'})

        self.pechuga.refresh_from_db()
        self.assertEqual(self.pechuga.stock, Decimal('0.000'))
        adjusted = sum(m.quantity for m in InventoryMovement.objects.filter(product=self.pechuga, movement_type='AUTO_ADJUST_IN'))
        self.assertEqual(adjusted, Decimal('0.600'))
