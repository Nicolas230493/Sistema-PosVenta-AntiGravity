from django.db import models
from django.contrib.auth.models import User
from products.models import Product
from customers.models import Customer

class Sale(models.Model):
    PAYMENT_METHODS = (
        ('CASH', 'Efectivo'),
        ('CC', 'Cuenta Corriente'),
        ('DEBIT', 'Tarjeta de Débito'),
        ('CREDIT', 'Tarjeta de Crédito'),
        ('TRANS', 'Transferencia'),
    )
    user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, verbose_name="Vendedor")
    customer = models.ForeignKey(Customer, on_delete=models.PROTECT, related_name='sales', verbose_name="Cliente", null=True, blank=True)
    date = models.DateTimeField(auto_now_add=True, verbose_name="Fecha")
    total_amount = models.DecimalField(max_digits=10, decimal_places=2, verbose_name="Total")
    tax_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0, verbose_name="Monto IVA")
    discount_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0, verbose_name="Descuento")
    surcharge_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0, verbose_name="Recargo")
    payment_method = models.CharField(max_length=10, choices=PAYMENT_METHODS, default='CASH', verbose_name="Método de Pago")

    def __str__(self):
        return f"Venta #{self.id}"

    class Meta:
        verbose_name = "Venta"
        verbose_name_plural = "Ventas"
        ordering = ['-date']

class SaleDetail(models.Model):
    sale = models.ForeignKey(Sale, related_name='details', on_delete=models.CASCADE)
    product = models.ForeignKey(Product, on_delete=models.SET_NULL, null=True)
    quantity = models.IntegerField(verbose_name="Cantidad")
    price = models.DecimalField(max_digits=10, decimal_places=2, verbose_name="Precio Unitario")
    tax_rate = models.DecimalField(max_digits=5, decimal_places=2, default=21.00, verbose_name="Tasa de IVA (%)")
    tax_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0, verbose_name="Monto IVA")
    subtotal = models.DecimalField(max_digits=10, decimal_places=2, verbose_name="Subtotal")

    def save(self, *args, **kwargs):
        self.subtotal = self.quantity * self.price
        super().save(*args, **kwargs)
