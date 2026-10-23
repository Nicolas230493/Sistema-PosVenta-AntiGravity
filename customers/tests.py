from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from finance.models import PaymentMethod
from .models import Customer, CurrentAccount, Payment


class CustomerPaymentTests(TestCase):
    def test_customer_payment_creates_only_one_current_account_entry(self):
        user = User.objects.create_user(username='seller', password='pass12345')
        self.client.force_login(user)
        customer = Customer.objects.create(
            full_name='Cliente con deuda',
            dni_cuit='20111222',
            balance=Decimal('300.00'),
        )
        cash = PaymentMethod.objects.create(name='Efectivo')

        response = self.client.post(reverse('customers:customer_payment', args=[customer.id]), {
            'amount': '100.00',
            'payment_method': str(cash.id),
            'notes': 'Pago parcial',
        })

        self.assertEqual(response.status_code, 302)
        customer.refresh_from_db()
        self.assertEqual(customer.balance, Decimal('200.00'))
        self.assertEqual(Payment.objects.count(), 1)
        self.assertEqual(CurrentAccount.objects.filter(customer=customer, entry_type='CREDIT').count(), 1)

    def test_customer_statement_pdf_exports(self):
        user = User.objects.create_user(username='seller2', password='pass12345')
        self.client.force_login(user)
        customer = Customer.objects.create(
            full_name='Cliente PDF',
            dni_cuit='44556677',
            balance=Decimal('50.00'),
        )

        response = self.client.get(reverse('customers:export_statement_pdf', args=[customer.id]))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/pdf')
        self.assertTrue(bytes(response.content).startswith(b'%PDF'))


class CustomerStatementTests(TestCase):
    """SPEC-CRM-10 §10.2: estado de cuenta (PDF + WhatsApp) desde CurrentAccount."""

    def setUp(self):
        from products.models import PriceList
        self.user = User.objects.create_user(username='cobrador', password='pass12345')
        self.client.force_login(self.user)
        self.customer = Customer.objects.create(full_name='Doña Rosa', dni_cuit='27111222', phone='+54 9 11 5555-1234',
                                                balance=Decimal('700.00'))
        CurrentAccount.objects.create(customer=self.customer, amount=Decimal('1000.00'), entry_type='DEBT',
                                      reference='Venta #10', balance_after=Decimal('1000.00'))
        CurrentAccount.objects.create(customer=self.customer, amount=Decimal('300.00'), entry_type='CREDIT',
                                      reference='Pago #3', balance_after=Decimal('700.00'))
        self.mayorista = PriceList.objects.create(name='Mayorista')

    def test_whatsapp_statement_has_chronological_detail_and_balance(self):
        from urllib.parse import unquote
        response = self.client.get(reverse('customers:whatsapp_statement', args=[self.customer.id]))

        self.assertEqual(response.status_code, 302)
        self.assertTrue(response['Location'].startswith('https://wa.me/5491155551234?text='))
        text = unquote(response['Location'])
        self.assertLess(text.index('Venta #10'), text.index('Pago #3'))
        self.assertIn('Compra fiada Venta #10: +$1000.00', text)
        self.assertIn('Entrega/Pago Pago #3: -$300.00', text)
        self.assertIn('SALDO DEUDOR ACTUAL: $700.00', text)

    def test_statement_pdf_uses_ledger(self):
        response = self.client.get(reverse('customers:export_statement_pdf', args=[self.customer.id]))

        self.assertEqual(response.status_code, 200)
        self.assertTrue(bytes(response.content).startswith(b'%PDF'))

    def test_customer_form_saves_price_list_segment(self):
        self.client.post(reverse('customers:customer_update', args=[self.customer.id]), {
            'full_name': 'Doña Rosa', 'dni_cuit': '27111222', 'price_list': self.mayorista.id,
        })

        self.customer.refresh_from_db()
        self.assertEqual(self.customer.price_list, self.mayorista)
