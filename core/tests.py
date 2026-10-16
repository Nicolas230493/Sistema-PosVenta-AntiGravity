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
