from datetime import date
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from finance.models import CashExpense, CashSession
from products.models import InventoryMovement, PriceLog, Product, PurchaseOrder, PurchaseOrderDetail

from .models import Supplier, SupplierAccountMovement
from .services import SupplierError, register_supplier_payment, weighted_average_cost


class WeightedAverageCostTests(TestCase):
    """SPEC-SUP-07 §7.1."""

    def test_formula(self):
        # (10 x 100 + 30 x 140) / 40 = 130
        self.assertEqual(weighted_average_cost(Decimal('10'), Decimal('100'), Decimal('30'), Decimal('140')), Decimal('130.00'))

    def test_negative_stock_counts_as_zero(self):
        self.assertEqual(weighted_average_cost(Decimal('-5'), Decimal('100'), Decimal('20'), Decimal('150')), Decimal('150.00'))

    def test_rounds_to_two_decimals(self):
        self.assertEqual(weighted_average_cost(Decimal('1'), Decimal('10'), Decimal('2'), Decimal('11')), Decimal('10.67'))


class PurchaseOrderReceptionTests(TestCase):
    """SPEC-SUP-07: recepción con CPP + cuenta corriente del proveedor."""

    def setUp(self):
        self.user = User.objects.create_user(username='compras', password='pass12345', is_staff=True)
        self.client.force_login(self.user)
        self.supplier = Supplier.objects.create(name='Avícola Granja')
        self.product = Product.objects.create(name='Cajón Pollo', unit='kg', price=Decimal('5000'),
                                              cost_price=Decimal('3000.00'), stock=Decimal('20.000'))
        self.order = PurchaseOrder.objects.create(supplier=self.supplier, status='ORDERED')
        PurchaseOrderDetail.objects.create(order=self.order, product=self.product, quantity=20, estimated_cost=Decimal('3600.00'))

    def _receive(self, **extra):
        return self.client.post(reverse('products:update_purchase_order_status', args=[self.order.id]),
                                {'status': 'RECEIVED', **extra})

    def test_receive_applies_cpp_and_registers_debt(self):
        self._receive(due_date='2026-12-01')

        self.product.refresh_from_db()
        self.order.refresh_from_db()
        self.supplier.refresh_from_db()
        self.assertEqual(self.order.status, 'RECEIVED')
        self.assertEqual(self.product.stock, Decimal('40.000'))
        self.assertEqual(self.product.cost_price, Decimal('3300.00'))  # (20x3000 + 20x3600) / 40
        log = PriceLog.objects.get(product=self.product, price_type='COST')
        self.assertEqual((log.old_price, log.new_price), (Decimal('3000.00'), Decimal('3300.00')))
        self.assertTrue(InventoryMovement.objects.filter(product=self.product, movement_type='PURCHASE_IN', quantity=20).exists())
        self.assertEqual(self.supplier.balance, Decimal('72000.00'))
        debit = SupplierAccountMovement.objects.get(supplier=self.supplier)
        self.assertEqual(debit.movement_type, 'DEBIT')
        self.assertEqual(debit.due_date, date(2026, 12, 1))
        self.assertEqual(debit.purchase_order, self.order)

    def test_order_cannot_be_received_twice(self):
        self._receive()
        self._receive()

        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, Decimal('40.000'))
        self.assertEqual(SupplierAccountMovement.objects.count(), 1)

    def test_received_order_cannot_be_cancelled(self):
        self._receive()
        self.client.post(reverse('products:update_purchase_order_status', args=[self.order.id]), {'status': 'CANCELLED'})

        self.order.refresh_from_db()
        self.assertEqual(self.order.status, 'RECEIVED')

    def test_pay_from_cash_creates_supplier_payment_expense(self):
        session = CashSession.objects.create(user=self.user, initial_amount=Decimal('100000'))
        self._receive()

        self.client.post(reverse('products:update_purchase_order_status', args=[self.order.id]),
                         {'status': 'PAID', 'source': 'CASH'})

        self.order.refresh_from_db()
        self.supplier.refresh_from_db()
        self.assertEqual(self.order.status, 'PAID')
        self.assertEqual(self.supplier.balance, Decimal('0.00'))
        expense = CashExpense.objects.get(session=session)
        self.assertEqual(expense.kind, CashExpense.KIND_SUPPLIER_PAYMENT)
        self.assertEqual(expense.amount, Decimal('72000.00'))
        credit = SupplierAccountMovement.objects.get(movement_type='CREDIT')
        self.assertEqual(credit.cash_expense, expense)

    def test_pay_from_bank_does_not_touch_cash(self):
        self._receive()

        self.client.post(reverse('suppliers:supplier_payment', args=[self.supplier.id]),
                         {'amount': '2000', 'source': 'BANK'})

        self.supplier.refresh_from_db()
        self.assertEqual(self.supplier.balance, Decimal('70000.00'))
        self.assertFalse(CashExpense.objects.exists())

    def test_cash_payment_requires_open_session(self):
        with self.assertRaises(SupplierError):
            register_supplier_payment(supplier=self.supplier, amount=Decimal('10'), source='CASH', user=self.user)
        self.assertFalse(SupplierAccountMovement.objects.exists())

    def test_supplier_account_page(self):
        self._receive()

        response = self.client.get(reverse('suppliers:supplier_account', args=[self.supplier.id]))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context['movements']), 1)
