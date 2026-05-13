from django.db import models
from django.contrib.auth.models import User
from products.models import Product
from customers.models import Customer
from finance.models import PaymentMethod

class Sale(models.Model):
    user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, verbose_name="Vendedor")
    customer = models.ForeignKey(Customer, on_delete=models.PROTECT, related_name='sales', verbose_name="Cliente", null=True, blank=True)
    date = models.DateTimeField(auto_now_add=True, verbose_name="Fecha")
    total_amount = models.DecimalField(max_digits=10, decimal_places=2, verbose_name="Total")
    tax_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0, verbose_name="Monto IVA")
    discount_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0, verbose_name="Descuento Manual")
    points_redeemed = models.IntegerField(default=0, verbose_name="Puntos Canjeados")
    points_discount = models.DecimalField(max_digits=10, decimal_places=2, default=0, verbose_name="Descuento por Puntos")
    promo_discount = models.DecimalField(max_digits=10, decimal_places=2, default=0, verbose_name="Descuento por Promos")
    surcharge_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0, verbose_name="Recargo")
    payment_method = models.ForeignKey(PaymentMethod, on_delete=models.PROTECT, null=True, verbose_name="Método de Pago")
    payment_method_old = models.CharField(max_length=10, null=True, blank=True) # Temporal para migración

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
    cost_price_at_sale = models.DecimalField(max_digits=10, decimal_places=2, default=0, verbose_name="Precio Costo (Histórico)")
    tax_rate = models.DecimalField(max_digits=5, decimal_places=2, default=21.00, verbose_name="Tasa de IVA (%)")
    tax_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0, verbose_name="Monto IVA")
    subtotal = models.DecimalField(max_digits=10, decimal_places=2, verbose_name="Subtotal")

    def save(self, *args, **kwargs):
        self.subtotal = self.quantity * self.price
        super().save(*args, **kwargs)

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
    quantity = models.IntegerField(verbose_name="Cantidad Devuelta")
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
