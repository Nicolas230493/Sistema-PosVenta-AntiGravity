from django import forms
from .models import Product, InventoryMovement

class ProductForm(forms.ModelForm):
    class Meta:
        model = Product
        fields = ['name', 'description', 'price', 'cost_price', 'stock', 'supplier']

class InventoryMovementForm(forms.ModelForm):
    class Meta:
        model = InventoryMovement
        fields = ['product', 'quantity', 'movement_type', 'reference']
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['movement_type'].initial = 'IN'

