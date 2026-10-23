from decimal import Decimal
from django import forms
from .models import Product, InventoryMovement, Category, ADJUSTMENT_REASONS

class CategoryForm(forms.ModelForm):
    class Meta:
        model = Category
        fields = ['name', 'description']
        widgets = {
            'description': forms.Textarea(attrs={'rows': 3}),
        }

class ProductForm(forms.ModelForm):
    adjustment_reason = forms.CharField(
        required=False,
        label="Motivo del Ajuste Manual",
        help_text="Requerido si se modifica el stock actual (ej: merma, desposte, vencimiento, conteo físico).",
        widget=forms.TextInput(attrs={'placeholder': 'Ej: Conteo físico, desposte, merma, rotura...'})
    )

    class Meta:
        model = Product
        fields = ['sku', 'barcode', 'name', 'unit', 'category', 'description', 'price', 'cost_price', 'peso_kg', 'tax_rate', 'stock', 'min_stock', 'expiry_date', 'supplier']
        widgets = {
            'expiry_date': forms.DateInput(attrs={'type': 'date'}),
        }
    
    def __init__(self, *args, user=None, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)
        self.fields['sku'].required = False
        self.fields['barcode'].required = False

    def clean_sku(self):
        sku = self.cleaned_data.get('sku')
        if sku:
            sku = sku.strip()
            if not sku.isalnum():
                raise forms.ValidationError("El SKU debe ser alfanumérico (solo letras y números) y no contener espacios.")
            return sku
        return None

    def clean_barcode(self):
        barcode = self.cleaned_data.get('barcode')
        if barcode:
            barcode = barcode.strip()
            return barcode
        return None

    def clean(self):
        cleaned_data = super().clean()
        unit = cleaned_data.get('unit')
        stock = cleaned_data.get('stock')
        min_stock = cleaned_data.get('min_stock')

        if unit in Product.DISCRETE_UNITS:
            if stock is not None and (stock % Decimal('1')) != Decimal('0'):
                self.add_error('stock', "Las unidades discretas (ej. 'unidad', 'caja') no pueden tener stock decimal.")
            if min_stock is not None and (min_stock % Decimal('1')) != Decimal('0'):
                self.add_error('min_stock', "El stock mínimo para unidades discretas no puede tener decimales.")

        # Auditoría de ajuste manual de stock si el producto ya existía
        if self.instance and self.instance.pk:
            old_stock = self.instance.stock
            new_stock = stock
            if new_stock is not None and old_stock is not None and new_stock != old_stock:
                reason = cleaned_data.get('adjustment_reason')
                if not reason or not reason.strip():
                    self.add_error('adjustment_reason', "Debe especificar el motivo del cambio manual de stock (ej: merma, desposte, vencimiento, conteo físico).")

        return cleaned_data

    def save(self, commit=True):
        is_update = bool(self.instance and self.instance.pk)
        old_stock = self.instance.stock if is_update else None
        product = super().save(commit=commit)

        if commit and is_update and old_stock is not None and product.stock != old_stock:
            diff = product.stock - old_stock
            reason = (self.cleaned_data.get('adjustment_reason') or '').strip() or 'Ajuste Manual'
            InventoryMovement.objects.create(
                product=product,
                quantity=abs(diff),
                movement_type='ADJ',
                reference=f"Ajuste Manual ({'+' if diff > 0 else '-'}{abs(diff)})",
                reason=reason,
                user=self.user,
            )
        return product

class InventoryMovementForm(forms.ModelForm):
    class Meta:
        model = InventoryMovement
        fields = ['product', 'quantity', 'cost_price', 'sale_price', 'reference', 'reason']
        labels = {
            'cost_price': 'Precio de Costo (Compra)',
            'sale_price': 'Precio de Venta (Unitario)',
            'reason': 'Motivo del Movimiento',
        }
        widgets = {
            'reason': forms.TextInput(attrs={'placeholder': 'Ej: Merma, Desposte, Vencimiento, Conteo físico, etc.'})
        }
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['cost_price'].required = True
        self.fields['sale_price'].required = True
        self.fields['reason'].required = True

    def clean(self):
        cleaned_data = super().clean()
        product = cleaned_data.get('product')
        quantity = cleaned_data.get('quantity')
        if product and quantity is not None:
            if product.is_discrete and (quantity % Decimal('1')) != Decimal('0'):
                self.add_error('quantity', "Este producto se mide por unidad entera y no admite cantidades decimales.")
        return cleaned_data

class StockAdjustmentForm(forms.Form):
    product = forms.ModelChoiceField(
        queryset=Product.objects.all().order_by('name'),
        label="Producto",
        widget=forms.Select(attrs={'class': 'form-select select2'})
    )
    ADJUST_TYPE_CHOICES = [
        ('SET', 'Establecer Stock Exacto (Conteo Físico)'),
        ('ADD', 'Sumar Stock (+ Entrada / Desposte)'),
        ('SUB', 'Restar Stock (- Merma / Vencimiento)'),
    ]
    adjustment_type = forms.ChoiceField(
        choices=ADJUST_TYPE_CHOICES,
        label="Tipo de Ajuste",
        widget=forms.Select(attrs={'class': 'form-select'})
    )
    quantity = forms.DecimalField(
        max_digits=10,
        decimal_places=3,
        min_value=Decimal('0.001'),
        label="Cantidad / Nuevo Stock",
        widget=forms.NumberInput(attrs={'class': 'form-control', 'step': 'any'})
    )
    reason = forms.CharField(
        max_length=255,
        required=True,
        label="Motivo Obligatorio",
        widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Ej: Merma, desposte, vencimiento, conteo físico...'})
    )

    def clean(self):
        cleaned_data = super().clean()
        product = cleaned_data.get('product')
        quantity = cleaned_data.get('quantity')
        adj_type = cleaned_data.get('adjustment_type')

        if product and quantity is not None:
            if product.is_discrete and (quantity % Decimal('1')) != Decimal('0'):
                self.add_error('quantity', f"El producto '{product.name}' es de unidad discreta y no admite decimales.")

            if adj_type == 'SUB' and quantity > product.stock:
                self.add_error('quantity', f"No se puede restar más stock ({quantity}) que el actual ({product.stock}).")

        return cleaned_data
