from django import forms
from .models import ConfiguracionSistema

class ConfiguracionForm(forms.ModelForm):
    class Meta:
        model = ConfiguracionSistema
        fields = ['nombre_empresa', 'color_primario', 'modo_oscuro', 'logo', 'dias_alerta_vencimiento',
                  'balanza_formato', 'balanza_digitos_plu']
        widgets = {
            'nombre_empresa': forms.TextInput(attrs={'class': 'form-control'}),
            'color_primario': forms.TextInput(attrs={'class': 'form-control form-control-color', 'type': 'color'}),
            'modo_oscuro': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'dias_alerta_vencimiento': forms.NumberInput(attrs={'class': 'form-control', 'min': 0}),
            'balanza_formato': forms.Select(attrs={'class': 'form-select'}),
            'balanza_digitos_plu': forms.Select(attrs={'class': 'form-select'}),
        }
