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

        self.client.post(reverse('finance:close_cash'), {'real_amount': '350.00'})

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
