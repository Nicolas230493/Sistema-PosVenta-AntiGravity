import os
import subprocess
import sys
from io import StringIO

from django.conf import settings
from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import TestCase

from products.models import Category
from suppliers.models import Supplier


class SettingsSecurityTests(TestCase):
    """SPEC-SEC-01: SECRET_KEY obligatoria y segura en producción."""

    def _load_settings(self, **env):
        full_env = {**os.environ, 'DEBUG': 'False', **env}
        return subprocess.run(
            [sys.executable, '-c', 'import pos_system.settings'],
            cwd=settings.BASE_DIR, env=full_env, capture_output=True, text=True,
        )

    def test_production_without_secret_key_fails(self):
        result = self._load_settings(SECRET_KEY='')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('ImproperlyConfigured', result.stderr)

    def test_production_with_insecure_key_fails(self):
        result = self._load_settings(SECRET_KEY='django-insecure-abc')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('ImproperlyConfigured', result.stderr)

    def test_production_with_short_key_fails(self):
        result = self._load_settings(SECRET_KEY='clave-corta-pero-no-insegura')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('ImproperlyConfigured', result.stderr)

    def test_production_with_strong_key_enables_https_settings(self):
        code = ('import pos_system.settings as s; '
                'assert s.SECURE_SSL_REDIRECT and s.SESSION_COOKIE_SECURE and s.CSRF_COOKIE_SECURE; '
                'assert s.SECURE_HSTS_SECONDS == 31536000')
        result = subprocess.run(
            [sys.executable, '-c', code], cwd=settings.BASE_DIR,
            env={**os.environ, 'DEBUG': 'False', 'SECRET_KEY': 'x' * 50}, capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)


class DeployCommandsTests(TestCase):
    def test_seed_initial_data_is_idempotent(self):
        call_command('seed_initial_data', stdout=StringIO())
        call_command('seed_initial_data', stdout=StringIO())

        self.assertEqual(Supplier.objects.filter(name__in=['Avícola Granja', 'Panificadora Central', 'Proveedor General']).count(), 3)
        self.assertEqual(Category.objects.filter(name__in=['Pollería', 'Panadería', 'Granja', 'Rebozados', 'Otros']).count(), 5)

    def test_ensure_superuser_reads_env_and_is_idempotent(self):
        env = {'DJANGO_SUPERUSER_USERNAME': 'jefe', 'DJANGO_SUPERUSER_PASSWORD': 'Clave-Segura-123'}
        with self._env(env):
            call_command('ensure_superuser', stdout=StringIO())
            call_command('ensure_superuser', stdout=StringIO())

        user = User.objects.get(username='jefe')
        self.assertTrue(user.is_superuser)
        self.assertTrue(user.check_password('Clave-Segura-123'))
        self.assertEqual(User.objects.filter(username='jefe').count(), 1)

    @staticmethod
    def _env(values):
        from unittest import mock
        return mock.patch.dict(os.environ, values)


class MiddlewareTests(TestCase):
    def test_unknown_url_returns_404_for_regular_user(self):
        user = User.objects.create_user(username='regular', password='pass12345')
        self.client.force_login(user)

        self.assertEqual(self.client.get('/no-existe/').status_code, 404)


class ExpiryAlertTests(TestCase):
    """SPEC-INV-09 §9.1: vencimientos próximos (N días configurables) y descuento con un clic."""

    def setUp(self):
        from datetime import timedelta
        from decimal import Decimal
        from django.contrib.auth.models import Group
        from django.utils import timezone
        from products.models import Product
        today = timezone.localdate()
        self.encargado = User.objects.create_user(username='enc_venc', password='pass12345')
        self.encargado.groups.add(Group.objects.get_or_create(name='Encargados')[0])
        self.cajero = User.objects.create_user(username='caj_venc', password='pass12345')
        self.cajero.groups.add(Group.objects.get_or_create(name='Cajero')[0])
        self.vencido = Product.objects.create(name='Leche', price=Decimal('100'), stock=5, expiry_date=today - timedelta(days=1))
        self.pronto = Product.objects.create(name='Yogur', price=Decimal('100'), stock=5, expiry_date=today + timedelta(days=7))
        self.lejano = Product.objects.create(name='Queso', price=Decimal('100'), stock=5, expiry_date=today + timedelta(days=8))
        self.sin_stock = Product.objects.create(name='Crema', price=Decimal('100'), stock=0, expiry_date=today)

    def test_dashboard_lists_expired_and_within_default_seven_days(self):
        from django.urls import reverse
        self.client.force_login(self.cajero)

        response = self.client.get(reverse('dashboard'))

        names = [p.name for p in response.context['vencimientos']]
        self.assertEqual(names, ['Leche', 'Yogur'])

    def test_alert_window_is_configurable(self):
        from django.urls import reverse
        from core.models import ConfiguracionSistema
        config = ConfiguracionSistema.get_config()
        config.dias_alerta_vencimiento = 10
        config.save()
        self.client.force_login(self.cajero)

        response = self.client.get(reverse('dashboard'))

        self.assertIn('Queso', [p.name for p in response.context['vencimientos']])

    def test_one_click_discount_creates_promotion_until_expiry(self):
        from decimal import Decimal
        from django.urls import reverse
        from sales.models import Promotion
        self.client.force_login(self.encargado)

        self.client.post(reverse('apply_expiry_discount', args=[self.pronto.id]), {'percent': '30'})
        self.client.post(reverse('apply_expiry_discount', args=[self.pronto.id]), {'percent': '50'})

        promo = Promotion.objects.get()
        self.assertEqual(promo.promo_type, 'PERCENT')
        self.assertEqual(promo.discount_percentage, Decimal('50'))
        self.assertEqual(promo.end_date, self.pronto.expiry_date)
        self.assertEqual(list(promo.products.all()), [self.pronto])

    def test_cashier_cannot_apply_discount(self):
        from django.urls import reverse
        from sales.models import Promotion
        self.client.force_login(self.cajero)

        self.client.post(reverse('apply_expiry_discount', args=[self.pronto.id]), {'percent': '30'})

        self.assertFalse(Promotion.objects.exists())
