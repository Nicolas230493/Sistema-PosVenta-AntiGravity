from django.db import models
from django.core.validators import MinValueValidator
from suppliers.models import Supplier
from decimal import Decimal

class Category(models.Model):
    name = models.CharField(max_length=100, verbose_name="Nombre de Categoría")
    description = models.TextField(verbose_name="Descripción", blank=True, null=True)
    
    def __str__(self):
        return self.name
    
    class Meta:
        verbose_name = "Categoría"
        verbose_name_plural = "Categorías"

class Product(models.Model):
    UNIT_CHOICES = [
        ('unidad', 'Unidad'),
        ('kg', 'Kilogramo'),
        ('litro', 'Litro'),
        ('lt', 'Litro (lt)'),
        ('m', 'Metro'),
        ('caja', 'Caja'),
        ('paquete', 'Paquete'),
    ]
    DISCRETE_UNITS = ('unidad', 'caja', 'paquete')
    FRACTIONAL_UNITS = ('kg', 'litro', 'lt', 'm')

    sku = models.CharField(max_length=50, unique=True, null=True, blank=True, verbose_name="SKU Interno")
    barcode = models.CharField(max_length=100, unique=True, null=True, blank=True, db_index=True, verbose_name="Código de Barras")
    # Código de producto (PLU) programado en la balanza: vincula sus etiquetas EAN-13 con peso/importe
    plu = models.PositiveIntegerField(unique=True, null=True, blank=True, verbose_name="PLU de balanza",
                                      help_text="Número del producto en la balanza que imprime etiquetas.")
    # Un producto con historial no se borra: se desactiva y deja de ofrecerse para operar
    active = models.BooleanField(default=True, db_index=True, verbose_name="Activo")
    name = models.CharField(max_length=200, verbose_name="Nombre")
    unit = models.CharField(max_length=10, choices=UNIT_CHOICES, default='unidad', verbose_name="Unidad")
    category = models.ForeignKey(Category, on_delete=models.SET_NULL, null=True, blank=True, related_name='products', verbose_name="Categoría")
    description = models.TextField(verbose_name="Descripción", blank=True, null=True)
    price = models.DecimalField(max_digits=12, decimal_places=2, validators=[MinValueValidator(0)], verbose_name="Precio Venta")
    cost_price = models.DecimalField(max_digits=12, decimal_places=2, default=0, validators=[MinValueValidator(0)], verbose_name="Precio Costo")
    stock = models.DecimalField(max_digits=10, decimal_places=3, default=0, validators=[MinValueValidator(0)], verbose_name="Stock")
    min_stock = models.DecimalField(max_digits=10, decimal_places=3, default=5, validators=[MinValueValidator(0)], verbose_name="Stock Mínimo")
    expiry_date = models.DateField(null=True, blank=True, verbose_name="Fecha de Vencimiento")
    peso_kg = models.DecimalField(max_digits=8, decimal_places=3, null=True, blank=True, verbose_name="Peso (kg)")
    supplier = models.ForeignKey(Supplier, on_delete=models.SET_NULL, null=True, blank=True, verbose_name="Proveedor")
    tax_rate = models.DecimalField(max_digits=5, decimal_places=2, default=21.00, verbose_name="Tasa de IVA (%)")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    @property
    def is_discrete(self):
        return self.unit in self.DISCRETE_UNITS

    @property
    def is_fractional(self):
        return self.unit in self.FRACTIONAL_UNITS

    def clean(self):
        from django.core.exceptions import ValidationError
        super().clean()
        if self.is_discrete:
            if self.stock is not None and (self.stock % Decimal('1')) != Decimal('0'):
                raise ValidationError({'stock': f"Las unidades discretas ({self.get_unit_display()}) no admiten decimales en el stock."})
            if self.min_stock is not None and (self.min_stock % Decimal('1')) != Decimal('0'):
                raise ValidationError({'min_stock': f"Las unidades discretas ({self.get_unit_display()}) no admiten decimales en el stock mínimo."})

    def save(self, *args, **kwargs):
        if self.sku == '':
            self.sku = None
        if self.barcode == '':
            self.barcode = None
        super().save(*args, **kwargs)

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
    ('ADJ', 'Ajuste Manual'),
    ('AUTO_ADJUST_IN', 'Ajuste Automático por Venta'),
    ('PURCHASE_IN', 'Ingreso por Compra/Reposición'),
    ('SALE_CANCEL_IN', 'Reversión por Anulación'),
]

ADJUSTMENT_REASONS = [
    ('merma', 'Merma'),
    ('desposte', 'Desposte'),
    ('vencimiento', 'Vencimiento'),
    ('conteo_fisico', 'Conteo Físico'),
    ('rotura', 'Rotura'),
    ('otro', 'Otro'),
]

class InventoryMovement(models.Model):
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name='movements')
    quantity = models.DecimalField(max_digits=10, decimal_places=3, verbose_name="Cantidad")
    cost_price = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True, verbose_name="Nuevo Precio Costo")
    sale_price = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True, verbose_name="Nuevo Precio Venta")
    movement_type = models.CharField(max_length=20, choices=MOVEMENT_TYPES, verbose_name="Tipo")
    reference = models.CharField(max_length=100, verbose_name="Referencia", help_text="Ej: Venta #123, Compra Factura X, Ajuste")
    reason = models.CharField(max_length=255, blank=True, default='', verbose_name="Motivo")
    date = models.DateTimeField(auto_now_add=True)
    user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True)

    def __init__(self, *args, **kwargs):
        if 'type' in kwargs and 'movement_type' not in kwargs:
            kwargs['movement_type'] = kwargs.pop('type')
        super().__init__(*args, **kwargs)

    @property
    def type(self):
        return self.movement_type

    @type.setter
    def type(self, value):
        self.movement_type = value

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
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name='losses')
    quantity = models.DecimalField(max_digits=10, decimal_places=3, validators=[MinValueValidator(0)], verbose_name="Cantidad")
    # Costo congelado al registrar la baja (valuación de mermas en la Ganancia Neta)
    unit_cost = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True, verbose_name="Costo Unitario")
    reason = models.CharField(max_length=3, choices=LOSS_REASONS, verbose_name="Motivo")
    date = models.DateTimeField(auto_now_add=True)
    notes = models.TextField(blank=True, null=True, verbose_name="Notas adicionales")
    user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True)

    def save(self, *args, **kwargs):
        if self.unit_cost is None:
            self.unit_cost = self.product.cost_price
        super().save(*args, **kwargs)

    def __str__(self):
        return f"Baja: {self.product.name} - {self.quantity} ({self.get_reason_display()})"

    class Meta:
        verbose_name = "Baja de Stock"
        verbose_name_plural = "Bajas de Stock"

class PriceLog(models.Model):
    PRICE_TYPES = [
        ('SALE', 'Precio de Venta'),
        ('COST', 'Precio de Costo'),
    ]
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name='price_logs')
    old_price = models.DecimalField(max_digits=10, decimal_places=2)
    new_price = models.DecimalField(max_digits=10, decimal_places=2)
    price_type = models.CharField(max_length=4, choices=PRICE_TYPES, default='SALE', verbose_name="Tipo de Precio")
    reason = models.CharField(max_length=255, blank=True, default='', verbose_name="Motivo")
    user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True)
    date = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-date']
        verbose_name = "Log de Precios"
        verbose_name_plural = "Logs de Precios"

class PriceList(models.Model):
    name = models.CharField(max_length=100, verbose_name="Nombre de Lista")
    active = models.BooleanField(default=True, verbose_name="Activa")

    def __str__(self):
        return self.name

    class Meta:
        verbose_name = "Lista de Precios"
        verbose_name_plural = "Listas de Precios"

class ProductPrice(models.Model):
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name='price_lists')
    price_list = models.ForeignKey(PriceList, on_delete=models.CASCADE, related_name='products')
    price = models.DecimalField(max_digits=10, decimal_places=2, verbose_name="Precio")

    def __str__(self):
        return f"{self.product.name} en {self.price_list.name}: ${self.price}"

    class Meta:
        unique_together = ('product', 'price_list')
        verbose_name = "Precio de Producto"
        verbose_name_plural = "Precios de Productos"

class Purchase(models.Model):
    supplier = models.ForeignKey(Supplier, on_delete=models.CASCADE, related_name='purchases', verbose_name="Proveedor")
    invoice_number = models.CharField(max_length=50, verbose_name="Número de Factura", blank=True, null=True)
    date = models.DateTimeField(auto_now_add=True, verbose_name="Fecha")
    total_amount = models.DecimalField(max_digits=10, decimal_places=2, verbose_name="Total Compra", default=0)
    user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, verbose_name="Registrado por")

    @property
    def tax_amount(self):
        # Asumiendo 21% si no está desglosado en la compra
        return self.total_amount - (self.total_amount / Decimal('1.21'))
    
    @property
    def net_amount(self):
        return self.total_amount - self.tax_amount

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

class PurchaseOrder(models.Model):
    STATUS_CHOICES = [
        ('PENDING', 'Pendiente'),
        ('ORDERED', 'Pedido'),
        ('RECEIVED', 'Recibido (Stock sumado)'),
        ('PAID', 'Pagado (Impacta Caja)'),
        ('CANCELLED', 'Cancelado'),
    ]
    supplier = models.ForeignKey(Supplier, on_delete=models.CASCADE, related_name='purchase_orders', verbose_name="Proveedor")
    date = models.DateTimeField(auto_now_add=True, verbose_name="Fecha")
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='PENDING', verbose_name="Estado")
    total_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0, verbose_name="Monto Estimado")
    notes = models.TextField(blank=True, null=True, verbose_name="Notas")

    def __str__(self):
        return f"Orden #{self.id} - {self.supplier.name} ({self.get_status_display()})"

    class Meta:
        verbose_name = "Orden de Compra"
        verbose_name_plural = "Órdenes de Compra"
        ordering = ['-date']

class PurchaseOrderDetail(models.Model):
    order = models.ForeignKey(PurchaseOrder, related_name='details', on_delete=models.CASCADE)
    product = models.ForeignKey(Product, on_delete=models.CASCADE, verbose_name="Producto")
    quantity = models.IntegerField(verbose_name="Cantidad")
    estimated_cost = models.DecimalField(max_digits=10, decimal_places=2, verbose_name="Costo Estimado")

    def __str__(self):
        return f"{self.product.name} ({self.quantity})"
