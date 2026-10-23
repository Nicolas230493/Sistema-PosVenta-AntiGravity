from decimal import Decimal

from django.db import models
from django.contrib.auth.models import User
from products.models import Product
from customers.models import Customer
from finance.models import PaymentMethod
from core.models import TurnoCaja
from finance.models import CashSession

class Sale(models.Model):
    STATUS_COMPLETED = 'COMPLETED'
    STATUS_RESERVED = 'RESERVED'
    STATUS_CANCELLED = 'CANCELLED'
    STATUS_CHOICES = [
        (STATUS_COMPLETED, 'Completada'),
        (STATUS_RESERVED, 'Apartado / Pedido con Seña'),
        (STATUS_CANCELLED, 'Anulada'),
    ]

    user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, verbose_name="Vendedor")
    customer = models.ForeignKey(Customer, on_delete=models.PROTECT, related_name='sales', verbose_name="Cliente", null=True, blank=True)
    turno = models.ForeignKey(TurnoCaja, on_delete=models.SET_NULL, null=True, blank=True, verbose_name="Turno de Caja")
    cash_session = models.ForeignKey(CashSession, on_delete=models.SET_NULL, null=True, blank=True, related_name='sales', verbose_name="Sesión de Caja")
    client_uuid = models.UUIDField(null=True, blank=True, unique=True, verbose_name="UUID Cliente (Offline)")
    fecha_hora = models.DateTimeField(auto_now_add=True, verbose_name="Fecha y Hora")
    total_amount = models.DecimalField(max_digits=10, decimal_places=2, verbose_name="Total")
    tax_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0, verbose_name="Monto IVA")
    discount_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0, verbose_name="Descuento Manual")
    points_redeemed = models.IntegerField(default=0, verbose_name="Puntos Canjeados")
    points_discount = models.DecimalField(max_digits=10, decimal_places=2, default=0, verbose_name="Descuento por Puntos")
    promo_discount = models.DecimalField(max_digits=10, decimal_places=2, default=0, verbose_name="Descuento por Promos")
    surcharge_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0, verbose_name="Recargo")
    # Método principal (el de mayor monto). El detalle de cobro está en SalePayment.
    payment_method = models.ForeignKey(PaymentMethod, on_delete=models.PROTECT, null=True, verbose_name="Método de Pago")
    payment_method_old = models.CharField(max_length=10, null=True, blank=True) # Temporal para migración
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=STATUS_COMPLETED, db_index=True, verbose_name="Estado")
    is_cancelled = models.BooleanField(default=False, verbose_name="Anulada")
    cancel_reason = models.TextField(blank=True, null=True, verbose_name="Motivo de Anulación")
    cancelled_at = models.DateTimeField(null=True, blank=True, verbose_name="Fecha de Anulación")
    cancelled_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='cancelled_sales', verbose_name="Anulada por")

    @property
    def net_amount(self):
        return self.total_amount - self.tax_amount

    @property
    def amount_paid(self):
        return self.payments.aggregate(total=models.Sum('amount'))['total'] or Decimal('0.00')

    @property
    def balance_due(self):
        return self.total_amount - self.amount_paid

    @property
    def is_mixed_payment(self):
        return self.payments.count() > 1

    def save(self, *args, **kwargs):
        if self.status == self.STATUS_CANCELLED:
            self.is_cancelled = True
        elif self.is_cancelled:
            self.status = self.STATUS_CANCELLED
        super().save(*args, **kwargs)

    def __str__(self):
        return f"Venta #{self.id}"

    class Meta:
        verbose_name = "Venta"
        verbose_name_plural = "Ventas"
        ordering = ['-fecha_hora']

class SaleDetail(models.Model):
    sale = models.ForeignKey(Sale, related_name='details', on_delete=models.CASCADE)
    product = models.ForeignKey(Product, on_delete=models.SET_NULL, null=True)
    quantity = models.DecimalField(max_digits=10, decimal_places=3, verbose_name="Cantidad")
    peso_kg = models.DecimalField(max_digits=8, decimal_places=3, null=True, blank=True, verbose_name="Peso (kg)")
    price = models.DecimalField(max_digits=10, decimal_places=2, verbose_name="Precio Unitario")
    # SPEC-FIN-06 §6.2: costo congelado al momento de la venta (rentabilidad histórica)
    unit_cost = models.DecimalField(max_digits=12, decimal_places=2, default=0, verbose_name="Costo Unitario (Histórico)")
    tax_rate = models.DecimalField(max_digits=5, decimal_places=2, default=21.00, verbose_name="Tasa de IVA (%)")
    tax_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0, verbose_name="Monto IVA")
    subtotal = models.DecimalField(max_digits=10, decimal_places=2, verbose_name="Subtotal")

    def save(self, *args, **kwargs):
        self.subtotal = self.quantity * self.price
        super().save(*args, **kwargs)

class SalePayment(models.Model):
    """SPEC-POS-08 §8.1: una venta puede cobrarse con varios medios de pago."""
    sale = models.ForeignKey(Sale, related_name='payments', on_delete=models.CASCADE, verbose_name="Venta")
    payment_method = models.ForeignKey(PaymentMethod, on_delete=models.PROTECT, verbose_name="Método de Pago")
    amount = models.DecimalField(max_digits=12, decimal_places=2, verbose_name="Monto")
    reference = models.CharField(max_length=100, blank=True, default='', verbose_name="N° de Comprobante")
    # Turno en el que ingresó el dinero (la seña y el saldo de un apartado pueden cobrarse en turnos distintos)
    cash_session = models.ForeignKey(CashSession, on_delete=models.SET_NULL, null=True, blank=True, related_name='sale_payments', verbose_name="Sesión de Caja")
    date = models.DateTimeField(auto_now_add=True, verbose_name="Fecha")
    user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, verbose_name="Cobrado por")

    def __str__(self):
        return f"{self.payment_method} ${self.amount} (Venta #{self.sale_id})"

    class Meta:
        verbose_name = "Pago de Venta"
        verbose_name_plural = "Pagos de Venta"
        ordering = ['date', 'id']


class SaleReturn(models.Model):
    sale = models.ForeignKey(Sale, on_delete=models.CASCADE, related_name='returns', verbose_name="Venta Original")
    date = models.DateTimeField(auto_now_add=True, verbose_name="Fecha de Devolución")
    reason = models.TextField(blank=True, null=True, verbose_name="Motivo")
    total_amount = models.DecimalField(max_digits=10, decimal_places=2, verbose_name="Total Devuelto")
    user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, verbose_name="Procesado por")

    def __str__(self):
        return f"Devolución #{self.id} de Venta #{self.sale.id}"

    class Meta:
        verbose_name = "Devolución"
        verbose_name_plural = "Devoluciones"
        ordering = ['-date']

class SaleReturnDetail(models.Model):
    sale_return = models.ForeignKey(SaleReturn, related_name='details', on_delete=models.CASCADE)
    product = models.ForeignKey(Product, on_delete=models.PROTECT)
    quantity = models.DecimalField(max_digits=10, decimal_places=3, verbose_name="Cantidad Devuelta")
    price_at_return = models.DecimalField(max_digits=10, decimal_places=2, verbose_name="Precio Unitario")

    def __str__(self):
        return f"{self.product.name} ({self.quantity})"

class Promotion(models.Model):
    TYPES = [
        ('PERCENT', 'Descuento Porcentual (%)'),
        ('FIXED_QTY', 'Precio Fijo por Cantidad (Ej: 2x1)'),
        ('DAY_DISCOUNT', 'Descuento por Día de la Semana'),
    ]
    name = models.CharField(max_length=100, verbose_name="Nombre de la Promo")
    promo_type = models.CharField(max_length=20, choices=TYPES, verbose_name="Tipo de Promo")
    
    # Aplicación
    products = models.ManyToManyField(Product, blank=True, related_name='promotions', verbose_name="Productos Incluidos")
    categories = models.ManyToManyField('products.Category', blank=True, related_name='promotions', verbose_name="Categorías Incluidas")
    
    # Valores
    discount_percentage = models.DecimalField(max_digits=5, decimal_places=2, default=0, verbose_name="Porcentaje Descuento")
    fixed_qty = models.IntegerField(default=0, verbose_name="Cantidad Requerida (Ej: 2)")
    fixed_price = models.DecimalField(max_digits=10, decimal_places=2, default=0, verbose_name="Precio Total del Pack")
    day_of_week = models.IntegerField(null=True, blank=True, choices=[(0, 'Lunes'), (1, 'Martes'), (2, 'Miércoles'), (3, 'Jueves'), (4, 'Viernes'), (5, 'Sábado'), (6, 'Domingo')], verbose_name="Día de Aplicación")

    # Vigencia
    start_date = models.DateField(verbose_name="Fecha Inicio")
    end_date = models.DateField(verbose_name="Fecha Fin")
    active = models.BooleanField(default=True, verbose_name="Activa")

    def __str__(self):
        return f"{self.name} ({self.get_promo_type_display()})"

    class Meta:
        verbose_name = "Promoción"
        verbose_name_plural = "Promociones"
