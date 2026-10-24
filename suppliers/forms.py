from decimal import Decimal
from django import forms
from .models import Supplier
from products.models import Product

class SupplierForm(forms.ModelForm):
    class Meta:
        model = Supplier
        fields = ['name', 'phone', 'email', 'address']


class SupplierRemitoEntryForm(forms.Form):
    supplier = forms.ModelChoiceField(
        queryset=Supplier.objects.all().order_by('name'),
        label="Proveedor",
        widget=forms.Select(attrs={'class': 'form-select'})
    )
    product = forms.ModelChoiceField(
        queryset=Product.objects.filter(active=True).order_by('name'),
        label="Producto",
        widget=forms.Select(attrs={'class': 'form-select'})
    )
    remito_number = forms.CharField(
        max_length=50,
        required=False,
        label="N° de Remito / Factura",
        widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Ej: R-0001-0004523'})
    )
    quantity = forms.DecimalField(
        max_digits=10,
        decimal_places=3,
        min_value=Decimal('0.001'),
        label="Cantidad Ingresada",
        widget=forms.NumberInput(attrs={'class': 'form-control', 'step': 'any'})
    )
    cost_price = forms.DecimalField(
        max_digits=12,
        decimal_places=2,
        required=False,
        min_value=Decimal('0.00'),
        label="Precio Costo Unitario ($)",
        widget=forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01', 'placeholder': 'Opcional (actualiza costo)'})
    )
    notes = forms.CharField(
        max_length=255,
        required=False,
        label="Observaciones / Motivo",
        widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Ej: Entrada de remito semanal'})
    )

    def clean(self):
        cleaned_data = super().clean()
        product = cleaned_data.get('product')
        quantity = cleaned_data.get('quantity')
        if product and quantity is not None:
            if product.is_discrete and (quantity % Decimal('1')) != Decimal('0'):
                self.add_error('quantity', f"El producto '{product.name}' se maneja por unidades enteras y no admite decimales.")
        return cleaned_data
