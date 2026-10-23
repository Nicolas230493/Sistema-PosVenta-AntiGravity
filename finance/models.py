from django.db import models
from django.contrib.auth.models import User
from django.core.validators import MinValueValidator

class PaymentMethod(models.Model):
    name = models.CharField(max_length=50, unique=True, verbose_name="Nombre")
    is_digital = models.BooleanField(default=False, verbose_name="¿Es Digital/Banco?")
    active = models.BooleanField(default=True, verbose_name="Activo")

    def __str__(self):
        return self.name

    class Meta:
        verbose_name = "Método de Pago"
        verbose_name_plural = "Métodos de Pago"

class CashSession(models.Model):
    user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, verbose_name="Cajero")
    start_date = models.DateTimeField(auto_now_add=True, verbose_name="Apertura")
    end_date = models.DateTimeField(null=True, blank=True, verbose_name="Cierre")
    initial_amount = models.DecimalField(max_digits=10, decimal_places=2, validators=[MinValueValidator(0)], verbose_name="Monto Inicial")
    total_sales_cash = models.DecimalField(max_digits=10, decimal_places=2, default=0, validators=[MinValueValidator(0)], verbose_name="Ventas Efectivo")
    total_sales_digital = models.DecimalField(max_digits=10, decimal_places=2, default=0, validators=[MinValueValidator(0)], verbose_name="Ventas Digital/Otros")
    total_expenses = models.DecimalField(max_digits=10, decimal_places=2, default=0, validators=[MinValueValidator(0)], verbose_name="Egresos")
    expected_final_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0, verbose_name="Final Esperado")
    real_final_amount = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, validators=[MinValueValidator(0)], verbose_name="Final Real")
    is_open = models.BooleanField(default=True, verbose_name="¿Caja Abierta?")
    notes = models.TextField(null=True, blank=True, verbose_name="Observaciones")
    # SPEC-FIN-06 §6.1: arqueo ciego. Lo declarado lo informa el cajero; lo esperado se calcula en el servidor al cerrar.
    declared_cash = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True, validators=[MinValueValidator(0)], verbose_name="Efectivo Declarado")
    declared_digital = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True, validators=[MinValueValidator(0)], verbose_name="Digital Declarado")
    expected_cash = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True, verbose_name="Efectivo Esperado")
    expected_digital = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True, verbose_name="Digital Esperado")
    difference = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True, verbose_name="Diferencia (Sobrante/Faltante)")

    @classmethod
    def get_active(cls):
        """SPEC-POS-03 §4.2: turno activo = última sesión abierta, sin filtrar por fecha ni usuario."""
        return cls.objects.filter(is_open=True).order_by('-id').first()

    def __str__(self):
        return f"Caja {self.id} - {self.user.username if self.user else 'N/A'} ({'Abierta' if self.is_open else 'Cerrada'})"

    class Meta:
        verbose_name = "Arqueo de Caja"
        verbose_name_plural = "Arqueos de Caja"
        ordering = ['-start_date']
        constraints = [
            models.UniqueConstraint(
                fields=['is_open'],
                condition=models.Q(is_open=True),
                name='unique_open_cash_session'
            )
        ]

class CashExpense(models.Model):
    KIND_EXPENSE = 'EXPENSE'
    KIND_SUPPLIER_PAYMENT = 'SUPPLIER_PAYMENT'
    KINDS = [
        (KIND_EXPENSE, 'Gasto de Caja'),
        (KIND_SUPPLIER_PAYMENT, 'Pago a Proveedor'),
    ]
    session = models.ForeignKey(CashSession, related_name='expenses', on_delete=models.CASCADE)
    amount = models.DecimalField(max_digits=10, decimal_places=2, validators=[MinValueValidator(0)], verbose_name="Monto")
    description = models.CharField(max_length=255, verbose_name="Concepto")
    # Los pagos a proveedores salen de la caja pero no son gasto: el costo ya está en el CMV
    kind = models.CharField(max_length=20, choices=KINDS, default=KIND_EXPENSE, verbose_name="Tipo")
    date = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Egreso: {self.description} (${self.amount})"
