from django import forms
from .models import CashMovement

class CashMovementForm(forms.ModelForm):
    class Meta:
        model = CashMovement
        fields = ['type', 'amount', 'description']
