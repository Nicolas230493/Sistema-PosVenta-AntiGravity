from django import forms
from .models import ConfiguracionSistema

class ConfiguracionForm(forms.ModelForm):
    class Meta:
        model = ConfiguracionSistema
        fields = ['nombre_empresa', 'color_primario', 'modo_oscuro', 'logo']
        widgets = {
            'nombre_empresa': forms.TextInput(attrs={'class': 'form-control'}),
            'color_primario': forms.TextInput(attrs={'class': 'form-control form-control-color', 'type': 'color'}),
            'modo_oscuro': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }
