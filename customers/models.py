from django.db import models
from django.core.validators import MinValueValidator
from finance.models import PaymentMethod

class Customer(models.Model):
    full_name = models.CharField(max_length=255, verbose_name="Nombre Completo")
    dni_cuit = models.CharField(max_length=20, unique=True, null=True, blank=True, verbose_name="DNI / CUIT")
    phone = models.CharField(max_length=20, null=True, blank=True, verbose_name="Teléfono")
    email = models.EmailField(null=True, blank=True, verbose_name="Email")
    address = models.CharField(max_length=255, null=True, blank=True, verbose_name="Dirección")
    balance = models.DecimalField(max_digits=10, decimal_places=2, default=0, validators=[MinValueValidator(0)], verbose_name="Saldo Deudor")
    points = models.IntegerField(default=0, validators=[MinValueValidator(0)], verbose_name="Puntos Acumulados")
    limite_credito = models.DecimalField(max_digits=10, decimal_places=2, default=0, validators=[MinValueValidator(0)], verbose_name="Límite de Crédito")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Fecha de Registro")

    def __str__(self):
        return self.full_name

    class Meta:
        verbose_name = "Cliente"
        verbose_name_plural = "Clientes"
        ordering = ['full_name']

class Payment(models.Model):
    customer = models.ForeignKey(Customer, on_delete=models.CASCADE, related_name='payments', verbose_name="Cliente")
    amount = models.DecimalField(max_digits=10, decimal_places=2, validators=[MinValueValidator(0)], verbose_name="Monto Pagado")
    payment_method = models.ForeignKey(PaymentMethod, on_delete=models.PROTECT, null=True, verbose_name="Método de Pago")
    payment_method_old = models.CharField(max_length=10, null=True, blank=True) # Temporal
    date = models.DateTimeField(auto_now_add=True, verbose_name="Fecha")
    notes = models.CharField(max_length=255, blank=True, null=True, verbose_name="Notas")
    user = models.ForeignKey('auth.User', on_delete=models.SET_NULL, null=True, verbose_name="Registrado por")

    def __str__(self):
        return f"Pago #{self.id} - {self.customer.full_name} (${self.amount})"

    class Meta:
        verbose_name = "Pago de Cliente"
        verbose_name_plural = "Pagos de Clientes"
        ordering = ['-date']

class CurrentAccount(models.Model):
    TYPES = (
        ('DEBT', 'Deuda (Venta)'),
        ('CREDIT', 'Crédito (Pago)'),
    )
    customer = models.ForeignKey(Customer, on_delete=models.CASCADE, related_name='account_ledger', verbose_name="Cliente")
    date = models.DateTimeField(auto_now_add=True, verbose_name="Fecha")
    amount = models.DecimalField(max_digits=10, decimal_places=2, verbose_name="Monto")
    entry_type = models.CharField(max_length=10, choices=TYPES, verbose_name="Tipo de Entrada")
    reference = models.CharField(max_length=100, blank=True, null=True, verbose_name="Referencia (Venta/Pago #)")
    balance_after = models.DecimalField(max_digits=10, decimal_places=2, verbose_name="Saldo Resultante")

    def __str__(self):
        return f"{self.customer.full_name} - {self.entry_type} - ${self.amount}"

    class Meta:
        verbose_name = "Cuenta Corriente"
        verbose_name_plural = "Movimientos de Cuenta Corriente"
        ordering = ['-date']
