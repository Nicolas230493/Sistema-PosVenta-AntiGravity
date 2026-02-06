from django.db import models
from django.contrib.auth.models import User

class CashMovement(models.Model):
    TYPE_CHOICES = (
        ('IN', 'Ingreso'),
        ('OUT', 'Egreso'),
    )
    type = models.CharField(max_length=3, choices=TYPE_CHOICES, verbose_name="Tipo")
    amount = models.DecimalField(max_digits=10, decimal_places=2, verbose_name="Monto")
    description = models.CharField(max_length=255, verbose_name="Descripción")
    date = models.DateTimeField(auto_now_add=True)
    user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True)

    def __str__(self):
        return f"{self.get_type_display()} - {self.amount}"

    class Meta:
        verbose_name = "Movimiento de Caja"
        verbose_name_plural = "Movimientos de Caja"
        ordering = ['-date']

class DailyClose(models.Model):
    date = models.DateTimeField(auto_now_add=True)
    total_income = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    total_expense = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    final_balance = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    closed_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True)

    class Meta:
        verbose_name = "Cierre de Caja"
        verbose_name_plural = "Cierres de Caja"
        ordering = ['-date']
