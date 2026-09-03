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
