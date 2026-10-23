from datetime import datetime, time, timedelta, timezone as dt_timezone
from decimal import Decimal
from io import BytesIO

from django.contrib.auth.models import User
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from openpyxl import load_workbook

from customers.models import Customer
from products.models import Product, Purchase
from sales.models import Sale
from suppliers.models import Supplier
from .models import CashExpense, CashSession, PaymentMethod


class CashValidationTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='cashier', password='pass12345', is_staff=True)
        self.client.force_login(self.user)

    def test_open_cash_rejects_negative_initial_amount(self):
        self.client.post(reverse('finance:open_cash'), {'amount': '-100.00'})

        self.assertEqual(CashSession.objects.count(), 0)

    def test_expense_rejects_non_positive_amount(self):
        CashSession.objects.create(user=self.user, initial_amount=Decimal('100.00'))

        self.client.post(reverse('finance:add_expense'), {
            'amount': '-1.00',
            'description': 'Ajuste invalido',
        })

        self.assertEqual(CashExpense.objects.count(), 0)

    def test_cash_report_pdf_exports_without_payment_method_key_error(self):
        session = CashSession.objects.create(
            user=self.user,
            initial_amount=Decimal('100.00'),
            expected_final_amount=Decimal('200.00'),
        )
        cash = PaymentMethod.objects.create(name='Efectivo')
        card = PaymentMethod.objects.create(name='Tarjeta', is_digital=True)
        customer = Customer.objects.create(full_name='Cliente', dni_cuit='99887766')
        Sale.objects.create(user=self.user, customer=customer, total_amount=Decimal('100.00'), payment_method=cash)
        Sale.objects.create(user=self.user, customer=customer, total_amount=Decimal('50.00'), payment_method=card)

        response = self.client.get(reverse('finance:export_cash_report', args=[session.id]))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/pdf')
        self.assertTrue(bytes(response.content).startswith(b'%PDF'))

    def test_fiscal_report_exports_valid_excel(self):
        supplier = Supplier.objects.create(name='Proveedor Fiscal')
        Purchase.objects.create(supplier=supplier, total_amount=Decimal('121.00'), user=self.user)

        response = self.client.get(reverse('finance:fiscal_reports'), {'export': '1'})

        self.assertEqual(response.status_code, 200)
        workbook = load_workbook(BytesIO(response.content))
        self.assertIn('IVA Ventas', workbook.sheetnames)
        self.assertIn('IVA Compras', workbook.sheetnames)


class CashRoleAndTotalsTests(TestCase):
    def setUp(self):
        from django.contrib.auth.models import Group
        self.cajero = User.objects.create_user(username='solo_cajero', password='pass12345')
        self.cajero.groups.add(Group.objects.get_or_create(name='Cajero')[0])
        self.encargado = User.objects.create_user(username='encargado', password='pass12345')
        self.encargado.groups.add(Group.objects.get_or_create(name='Encargados')[0])
        self.cash = PaymentMethod.objects.create(name='Efectivo')

    def test_cashier_cannot_access_financial_reports_or_expenses(self):
        session = CashSession.objects.create(user=self.cajero, initial_amount=Decimal('100'))
        self.client.force_login(self.cajero)

        self.assertEqual(self.client.get(reverse('finance:fiscal_reports')).status_code, 302)
        self.assertEqual(self.client.get(reverse('finance:export_cash_report', args=[session.id])).status_code, 302)
        self.client.post(reverse('finance:add_expense'), {'amount': '10', 'description': 'x'})
        self.assertEqual(CashExpense.objects.count(), 0)

    def test_manager_group_can_access_fiscal_reports(self):
        self.client.force_login(self.encargado)

        self.assertEqual(self.client.get(reverse('finance:fiscal_reports')).status_code, 200)

    def test_close_cash_recomputes_expected_amount(self):
        session = CashSession.objects.create(user=self.cajero, initial_amount=Decimal('100'))
        customer = Customer.objects.create(full_name='Cliente', dni_cuit='11223344')
        Sale.objects.create(user=self.encargado, customer=customer, total_amount=Decimal('250.00'),
                            payment_method=self.cash, cash_session=session)
        self.client.force_login(self.cajero)

        self.client.post(reverse('finance:close_cash'), {'declared_cash': '350.00', 'declared_digital': '0'})

        session.refresh_from_db()
        self.assertFalse(session.is_open)
        self.assertEqual(session.expected_final_amount, Decimal('350.00'))


class CashSessionIntegrityTests(TestCase):
    """SPEC-POS-03: una única caja abierta, detectada solo por estado."""

    def setUp(self):
        self.user = User.objects.create_user(username='turno', password='pass12345')

    def test_get_active_ignores_utc_date_boundary(self):
        # 22:30 hora de Buenos Aires = 01:30 UTC del día siguiente
        local_tz = timezone.get_current_timezone()
        late_local = datetime.combine(timezone.localdate(), time(22, 30), tzinfo=local_tz)
        self.assertNotEqual(late_local.astimezone(dt_timezone.utc).date(), late_local.date())

        session = CashSession.objects.create(user=self.user, initial_amount=Decimal('0'))
        CashSession.objects.filter(pk=session.pk).update(start_date=late_local)

        self.assertEqual(CashSession.get_active(), session)

    def test_get_active_returns_session_opened_days_ago(self):
        session = CashSession.objects.create(user=self.user, initial_amount=Decimal('0'))
        CashSession.objects.filter(pk=session.pk).update(start_date=timezone.now() - timedelta(days=3))

        self.assertEqual(CashSession.get_active(), session)

    def test_get_active_returns_none_when_all_closed(self):
        CashSession.objects.create(user=self.user, initial_amount=Decimal('0'), is_open=False)

        self.assertIsNone(CashSession.get_active())

    def test_second_open_session_raises_integrity_error(self):
        CashSession.objects.create(user=self.user, initial_amount=Decimal('0'))
        other = User.objects.create_user(username='otro', password='pass12345')

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                CashSession.objects.create(user=other, initial_amount=Decimal('0'))

    def test_multiple_closed_sessions_are_allowed(self):
        CashSession.objects.create(user=self.user, initial_amount=Decimal('0'), is_open=False)
        CashSession.objects.create(user=self.user, initial_amount=Decimal('0'), is_open=False)
        CashSession.objects.create(user=self.user, initial_amount=Decimal('0'))

        self.assertEqual(CashSession.objects.count(), 3)


class BlindCloseTests(TestCase):
    """SPEC-FIN-06 §6.1: cierre de caja ciego."""

    def setUp(self):
        from django.contrib.auth.models import Group, Permission
        from products.models import Product
        self.cajero = User.objects.create_user(username='cajero_ciego', password='pass12345')
        self.cajero.groups.add(Group.objects.get_or_create(name='Cajeros')[0])
        self.cajero.user_permissions.add(Permission.objects.get(codename='add_sale', content_type__app_label='sales'))
        self.admin = User.objects.create_user(username='jefe', password='pass12345')
        self.admin.groups.add(Group.objects.get_or_create(name='Administrador')[0])
        self.cash = PaymentMethod.objects.create(name='Efectivo')
        self.qr = PaymentMethod.objects.create(name='QR', is_digital=True)
        self.session = CashSession.objects.create(user=self.cajero, initial_amount=Decimal('1000.00'))
        self.product = Product.objects.create(sku='POLLO', name='Pollo Entero', unit='kg', price=Decimal('4000.00'),
                                              cost_price=Decimal('2500.00'), stock=Decimal('1.000'))

    def _sell(self, weight, payments):
        import json
        self.client.force_login(self.cajero)
        return self.client.post(reverse('sales:pos'), {
            'cart_data': json.dumps([{'id': self.product.id, 'qty': 1, 'weight': weight}]),
            'payment_method': str(self.cash.id),
            'payments_data': json.dumps(payments),
            'price_list': 'default',
        }, headers={'X-POS-Ajax': '1'})

    def test_cashier_does_not_see_expected_cash(self):
        self._sell('1.000', [{'method_id': self.cash.id, 'amount': '1234.56'}, {'method_id': self.qr.id, 'amount': '2765.44'}])
        self.client.force_login(self.cajero)

        response = self.client.get(reverse('finance:close_cash'))

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['blind'])
        self.assertNotIn('expected', response.context)
        self.assertNotContains(response, '2234,56')  # 1000 inicial + 1234,56 efectivo (formato es-ar)
        self.assertNotContains(response, 'esperado</span>')

    def test_admin_sees_expected_cash(self):
        self._sell('1.000', [{'method_id': self.cash.id, 'amount': '1234.56'}, {'method_id': self.qr.id, 'amount': '2765.44'}])
        self.client.force_login(self.admin)

        response = self.client.get(reverse('finance:close_cash'))

        self.assertFalse(response.context['blind'])
        self.assertContains(response, '2234,56')

    def test_close_stores_declared_expected_and_difference(self):
        self._sell('1.000', [{'method_id': self.cash.id, 'amount': '1500'}, {'method_id': self.qr.id, 'amount': '2500'}])
        self.client.force_login(self.cajero)

        self.client.post(reverse('finance:close_cash'), {'declared_cash': '2400.00', 'declared_digital': '2500.00'})

        self.session.refresh_from_db()
        self.assertFalse(self.session.is_open)
        self.assertEqual(self.session.expected_cash, Decimal('2500.00'))
        self.assertEqual(self.session.expected_digital, Decimal('2500.00'))
        self.assertEqual(self.session.declared_cash, Decimal('2400.00'))
        self.assertEqual(self.session.declared_digital, Decimal('2500.00'))
        self.assertEqual(self.session.difference, Decimal('-100.00'))

    def test_close_requires_declared_digital(self):
        self.client.force_login(self.cajero)

        self.client.post(reverse('finance:close_cash'), {'declared_cash': '1000.00'})

        self.session.refresh_from_db()
        self.assertTrue(self.session.is_open)

    def test_cancelled_sale_is_not_expected_in_cash(self):
        from sales.services import cancel_sale
        self._sell('0.500', [{'method_id': self.cash.id, 'amount': '2000'}])
        cancel_sale(sale=Sale.objects.get(), user=self.admin, reason='Error')
        self.client.force_login(self.cajero)

        self.client.post(reverse('finance:close_cash'), {'declared_cash': '1000', 'declared_digital': '0'})

        self.session.refresh_from_db()
        self.assertEqual(self.session.expected_cash, Decimal('1000.00'))
        self.assertEqual(self.session.difference, Decimal('0.00'))

    def test_session_report_lists_forced_adjustments(self):
        # Stock 1 kg, se venden 3 kg: 2 kg de ajuste forzoso a $4000 = $8000
        self._sell('3.000', [{'method_id': self.cash.id, 'amount': '12000'}])
        self.client.force_login(self.cajero)
        self.client.post(reverse('finance:close_cash'), {'declared_cash': '13000', 'declared_digital': '0'})
        self.client.force_login(self.admin)

        response = self.client.get(reverse('finance:cash_session_report', args=[self.session.id]))

        self.assertEqual(response.status_code, 200)
        adjustments = response.context['adjustments']
        self.assertEqual(len(adjustments), 1)
        self.assertEqual(adjustments[0]['product'], 'Pollo Entero')
        self.assertEqual(adjustments[0]['quantity'], Decimal('2.000'))
        self.assertEqual(adjustments[0]['amount'], Decimal('8000.00'))
        self.assertEqual(response.context['adjustments_total'], Decimal('8000.00'))
        self.assertContains(response, 'Sin diferencias')

    def test_cashier_cannot_open_session_report(self):
        self.client.force_login(self.cajero)

        response = self.client.get(reverse('finance:cash_session_report', args=[self.session.id]))

        self.assertEqual(response.status_code, 302)


class NetProfitTests(TestCase):
    """SPEC-FIN-06 §6.2: Ganancia Neta = Ventas Netas - CMV - Gastos de Caja - Mermas."""

    def setUp(self):
        from products.models import Product, StockLoss
        from sales.models import SaleDetail
        self.user = User.objects.create_user(username='dueno', password='pass12345', is_staff=True)
        self.cash = PaymentMethod.objects.create(name='Efectivo')
        session = CashSession.objects.create(user=self.user, initial_amount=Decimal('0'))
        product = Product.objects.create(name='Pollo', unit='kg', price=Decimal('100'), cost_price=Decimal('70'), stock=10)

        sale = Sale.objects.create(user=self.user, total_amount=Decimal('1000.00'), payment_method=self.cash, cash_session=session)
        SaleDetail.objects.create(sale=sale, product=product, quantity=Decimal('10'), price=Decimal('100'), unit_cost=Decimal('60.00'))
        cancelled = Sale.objects.create(user=self.user, total_amount=Decimal('500.00'), payment_method=self.cash, status='CANCELLED')
        SaleDetail.objects.create(sale=cancelled, product=product, quantity=Decimal('5'), price=Decimal('100'), unit_cost=Decimal('60.00'))
        reserved = Sale.objects.create(user=self.user, total_amount=Decimal('300.00'), status='RESERVED')
        SaleDetail.objects.create(sale=reserved, product=product, quantity=Decimal('3'), price=Decimal('100'), unit_cost=Decimal('60.00'))

        CashExpense.objects.create(session=session, amount=Decimal('50.00'), description='Limpieza')
        CashExpense.objects.create(session=session, amount=Decimal('999.00'), description='Pago OC #1', kind=CashExpense.KIND_SUPPLIER_PAYMENT)
        StockLoss.objects.create(product=product, quantity=Decimal('1.5'), unit_cost=Decimal('40.00'), reason='EXP')

    def _range(self):
        today = timezone.localdate()
        tz = timezone.get_current_timezone()
        return datetime.combine(today, time.min, tzinfo=tz), datetime.combine(today + timedelta(days=1), time.min, tzinfo=tz)

    def test_net_profit_formula(self):
        from finance.services import net_profit
        report = net_profit(*self._range())

        self.assertEqual(report['net_sales'], Decimal('1000.00'))
        self.assertEqual(report['cmv'], Decimal('600.00'))
        self.assertEqual(report['expenses'], Decimal('50.00'))  # el pago a proveedor no es gasto
        self.assertEqual(report['losses'], Decimal('60.00'))
        self.assertEqual(report['net_profit'], Decimal('290.00'))  # 1000 - 600 - 50 - 60

    def test_returns_reduce_sales_and_cost(self):
        from finance.services import net_profit
        from sales.models import SaleReturn, SaleReturnDetail
        sale = Sale.objects.get(status='COMPLETED')
        sale_return = SaleReturn.objects.create(sale=sale, total_amount=Decimal('200.00'), user=self.user)
        SaleReturnDetail.objects.create(sale_return=sale_return, product=sale.details.get().product,
                                        quantity=Decimal('2'), price_at_return=Decimal('100'))

        report = net_profit(*self._range())

        self.assertEqual(report['net_sales'], Decimal('800.00'))
        self.assertEqual(report['cmv'], Decimal('480.00'))
        self.assertEqual(report['net_profit'], Decimal('210.00'))

    def test_profit_report_view(self):
        self.client.force_login(self.user)

        response = self.client.get(reverse('finance:profit_report'))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['report']['net_profit'], Decimal('290.00'))
