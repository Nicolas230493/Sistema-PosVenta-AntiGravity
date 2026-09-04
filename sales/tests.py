import json
from decimal import Decimal

from django.contrib.auth.models import User, Permission
from django.contrib.contenttypes.models import ContentType
from django.test import TestCase
from django.urls import reverse

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
