from django import forms
from .models import Product, InventoryMovement

class ProductForm(forms.ModelForm):
    class Meta:
        model = Product
        fields = ['sku', 'name', 'description', 'price', 'cost_price', 'stock', 'min_stock', 'expiry_date', 'supplier']
        widgets = {
            'expiry_date': forms.DateInput(attrs={'type': 'date'}),
        }
    
    def clean_sku(self):
        sku = self.cleaned_data.get('sku')
        if sku:
            if not sku.isalnum():
                raise forms.ValidationError("El SKU debe ser alfanumérico (solo letras y números) y no contener espacios.")
        return sku

class InventoryMovementForm(forms.ModelForm):
    class Meta:
        model = InventoryMovement
        fields = ['product', 'quantity', 'cost_price', 'sale_price', 'reference']
        labels = {
            'cost_price': 'Precio de Costo (Compra)',
            'sale_price': 'Precio de Venta (Unitario)',
        }
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Aseguramos que los campos de precio sean obligatorios para la entrada de stock
        self.fields['cost_price'].required = True
        self.fields['sale_price'].required = True
        # Aplicamos clases de Bootstrap a todos los campos si fuera necesario, 
        # aunque el template ya usa widget_tweaks.

