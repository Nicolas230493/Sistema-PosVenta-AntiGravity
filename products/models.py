from django.db import models
from suppliers.models import Supplier

class Category(models.Model):
    name = models.CharField(max_length=100, verbose_name="Nombre de Categoría")
    
    def __str__(self):
        return self.name
    
    class Meta:
        verbose_name = "Categoría"
        verbose_name_plural = "Categorías"

class Product(models.Model):
    sku = models.CharField(max_length=50, unique=True, default='000000', verbose_name="SKU / Código de Barras")
    name = models.CharField(max_length=200, verbose_name="Nombre")
    category = models.ForeignKey(Category, on_delete=models.SET_NULL, null=True, blank=True, related_name='products', verbose_name="Categoría")
    description = models.TextField(verbose_name="Descripción", blank=True, null=True)
    # ... rest of fields ...
    price = models.DecimalField(max_digits=10, decimal_places=2, verbose_name="Precio Venta")
    cost_price = models.DecimalField(max_digits=10, decimal_places=2, default=0, verbose_name="Precio Costo")
    stock = models.IntegerField(default=0, verbose_name="Stock")
    min_stock = models.IntegerField(default=5, verbose_name="Stock Mínimo")
    expiry_date = models.DateField(null=True, blank=True, verbose_name="Fecha de Vencimiento")
    supplier = models.ForeignKey(Supplier, on_delete=models.SET_NULL, null=True, verbose_name="Proveedor")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def is_low_stock(self):
        return self.stock <= self.min_stock

    @property
    def stock_status(self):
        if self.stock <= 0:
            return 'danger'
        if self.stock <= self.min_stock:
            return 'warning'
        return 'success'

    @property
    def stock_percentage(self):
        if self.min_stock <= 0:
            return 100
        # Calculamos el porcentaje respecto al doble del mínimo para dar margen visual
        percent = (self.stock / (self.min_stock * 2)) * 100
        return min(percent, 100)

    def __str__(self):
        return self.name
    
    class Meta:
        verbose_name = "Producto"
        verbose_name_plural = "Productos"

from django.contrib.auth.models import User

MOVEMENT_TYPES = [
    ('IN', 'Entrada'),
    ('OUT', 'Salida'),
    ('ADJ', 'Ajuste'),
]

class InventoryMovement(models.Model):
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name='movements')
    quantity = models.IntegerField(verbose_name="Cantidad")
    cost_price = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, verbose_name="Nuevo Precio Costo")
    sale_price = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, verbose_name="Nuevo Precio Venta")
    movement_type = models.CharField(max_length=3, choices=MOVEMENT_TYPES, verbose_name="Tipo")
    reference = models.CharField(max_length=100, verbose_name="Referencia", help_text="Ej: Venta #123, Compra Factura X, Ajuste")
    date = models.DateTimeField(auto_now_add=True)
    user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True)

    def __str__(self):
        return f"{self.movement_type} - {self.product.name} ({self.quantity})"
    
    class Meta:
        verbose_name = "Movimiento de Inventario"
        verbose_name_plural = "Movimientos de Inventario"
        ordering = ['-date']

LOSS_REASONS = [
    ('EXP', 'Vencimiento'),
    ('DAM', 'Rotura'),
    ('INT', 'Consumo Interno'),
    ('OTH', 'Otros'),
]

class StockLoss(models.Model):
    # ... (campos existentes)
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name='losses')
    quantity = models.PositiveIntegerField(verbose_name="Cantidad")
    reason = models.CharField(max_length=3, choices=LOSS_REASONS, verbose_name="Motivo")
    date = models.DateTimeField(auto_now_add=True)
    notes = models.TextField(blank=True, null=True, verbose_name="Notas adicionales")
    user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True)

    def __str__(self):
        return f"Baja: {self.product.name} - {self.quantity} ({self.get_reason_display()})"

    class Meta:
        verbose_name = "Baja de Stock"
        verbose_name_plural = "Bajas de Stock"

class PriceLog(models.Model):
    # ... (existente)
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name='price_logs')
    old_price = models.DecimalField(max_digits=10, decimal_places=2)
    new_price = models.DecimalField(max_digits=10, decimal_places=2)
    user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True)
    date = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-date']
        verbose_name = "Log de Precios"
        verbose_name_plural = "Logs de Precios"

class Purchase(models.Model):
    supplier = models.ForeignKey(Supplier, on_delete=models.CASCADE, related_name='purchases', verbose_name="Proveedor")
    invoice_number = models.CharField(max_length=50, verbose_name="Número de Factura", blank=True, null=True)
    date = models.DateTimeField(auto_now_add=True, verbose_name="Fecha")
    total_amount = models.DecimalField(max_digits=10, decimal_places=2, verbose_name="Total Compra", default=0)
    user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, verbose_name="Registrado por")

    def __str__(self):
        return f"Compra #{self.id} - {self.supplier.name}"

    class Meta:
        verbose_name = "Compra"
        verbose_name_plural = "Compras"
        ordering = ['-date']

class PurchaseDetail(models.Model):
    purchase = models.ForeignKey(Purchase, related_name='details', on_delete=models.CASCADE)
    product = models.ForeignKey(Product, on_delete=models.CASCADE, verbose_name="Producto")
    quantity = models.IntegerField(verbose_name="Cantidad")
    cost_price = models.DecimalField(max_digits=10, decimal_places=2, verbose_name="Precio de Costo")
    subtotal = models.DecimalField(max_digits=10, decimal_places=2, verbose_name="Subtotal")

    def save(self, *args, **kwargs):
        self.subtotal = self.quantity * self.cost_price
        super().save(*args, **kwargs)
