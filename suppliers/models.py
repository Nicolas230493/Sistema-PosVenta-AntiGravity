from django.contrib.auth.models import User
from django.db import models

class Supplier(models.Model):
    name = models.CharField(max_length=100, verbose_name="Nombre")
    phone = models.CharField(max_length=20, verbose_name="Teléfono", blank=True, null=True)
    email = models.EmailField(verbose_name="Email", blank=True, null=True)
    address = models.TextField(verbose_name="Dirección", blank=True, null=True)
    # SPEC-SUP-07 §7.2: saldo de la cuenta corriente (positivo = le debemos al proveedor)
    balance = models.DecimalField(max_digits=12, decimal_places=2, default=0, verbose_name="Saldo Adeudado")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Fecha de registro")

    def __str__(self):
        return self.name

    class Meta:
        verbose_name = "Proveedor"
        verbose_name_plural = "Proveedores"
        ordering = ['-created_at']


class SupplierAccountMovement(models.Model):
    """SPEC-SUP-07 §7.2: cuenta corriente con proveedores."""
    DEBIT = 'DEBIT'
    CREDIT = 'CREDIT'
    TYPES = [
        (DEBIT, 'Deuda (Mercadería recibida)'),
        (CREDIT, 'Pago'),
    ]
    SOURCE_CASH = 'CASH'
    SOURCE_BANK = 'BANK'
    SOURCES = [
        (SOURCE_CASH, 'Caja'),
        (SOURCE_BANK, 'Banco / Transferencia'),
    ]

    supplier = models.ForeignKey(Supplier, on_delete=models.CASCADE, related_name='account_movements', verbose_name="Proveedor")
    movement_type = models.CharField(max_length=10, choices=TYPES, verbose_name="Tipo")
    amount = models.DecimalField(max_digits=12, decimal_places=2, verbose_name="Monto")
    date = models.DateTimeField(auto_now_add=True, verbose_name="Fecha")
    due_date = models.DateField(null=True, blank=True, verbose_name="Vencimiento de Factura")
    reference = models.CharField(max_length=100, blank=True, default='', verbose_name="Referencia")
    purchase_order = models.ForeignKey('products.PurchaseOrder', on_delete=models.SET_NULL, null=True, blank=True, related_name='account_movements', verbose_name="Orden de Compra")
    payment_source = models.CharField(max_length=10, choices=SOURCES, blank=True, default='', verbose_name="Origen del Pago")
    cash_expense = models.OneToOneField('finance.CashExpense', on_delete=models.SET_NULL, null=True, blank=True, related_name='supplier_movement', verbose_name="Egreso de Caja")
    balance_after = models.DecimalField(max_digits=12, decimal_places=2, verbose_name="Saldo Resultante")
    notes = models.CharField(max_length=255, blank=True, default='', verbose_name="Notas")
    user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, verbose_name="Registrado por")

    def __str__(self):
        return f"{self.supplier.name} - {self.get_movement_type_display()} ${self.amount}"

    class Meta:
        verbose_name = "Movimiento de Cuenta de Proveedor"
        verbose_name_plural = "Cuenta Corriente de Proveedores"
        ordering = ['-date', '-id']
