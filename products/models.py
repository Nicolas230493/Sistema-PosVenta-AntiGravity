from django.db import models
from suppliers.models import Supplier

class Product(models.Model):
    sku = models.CharField(max_length=50, unique=True, blank=True, null=True, verbose_name="SKU / Código de Barras")
    name = models.CharField(max_length=200, verbose_name="Nombre")
    description = models.TextField(verbose_name="Descripción", blank=True, null=True)
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

class InventoryMovement(models.Model):
    # ... campos existentes ...
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
