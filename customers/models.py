from django.db import models

class Customer(models.Model):
    full_name = models.CharField(max_length=255, verbose_name="Nombre Completo")
    dni_cuit = models.CharField(max_length=20, unique=True, null=True, blank=True, verbose_name="DNI / CUIT")
    phone = models.CharField(max_length=20, null=True, blank=True, verbose_name="Teléfono")
    email = models.EmailField(null=True, blank=True, verbose_name="Email")
    address = models.CharField(max_length=255, null=True, blank=True, verbose_name="Dirección")
    balance = models.DecimalField(max_digits=10, decimal_places=2, default=0, verbose_name="Saldo Deudor")
    points = models.IntegerField(default=0, verbose_name="Puntos Acumulados")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Fecha de Registro")

    def __str__(self):
        return self.full_name

    class Meta:
        verbose_name = "Cliente"
        verbose_name_plural = "Clientes"
        ordering = ['full_name']

class Payment(models.Model):
    PAYMENT_METHODS = (
        ('CASH', 'Efectivo'),
        ('DEBIT', 'Tarjeta de Débito'),
        ('CREDIT', 'Tarjeta de Crédito'),
        ('TRANS', 'Transferencia'),
    )
    customer = models.ForeignKey(Customer, on_delete=models.CASCADE, related_name='payments', verbose_name="Cliente")
    amount = models.DecimalField(max_digits=10, decimal_places=2, verbose_name="Monto Pagado")
    payment_method = models.CharField(max_length=10, choices=PAYMENT_METHODS, default='CASH', verbose_name="Método de Pago")
    date = models.DateTimeField(auto_now_add=True, verbose_name="Fecha")
    notes = models.CharField(max_length=255, blank=True, null=True, verbose_name="Notas")
    user = models.ForeignKey('auth.User', on_delete=models.SET_NULL, null=True, verbose_name="Registrado por")

    def __str__(self):
        return f"Pago #{self.id} - {self.customer.full_name} (${self.amount})"

    class Meta:
        verbose_name = "Pago de Cliente"
        verbose_name_plural = "Pagos de Clientes"
        ordering = ['-date']
