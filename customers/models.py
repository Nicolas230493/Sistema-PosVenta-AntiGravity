from django.db import models

class Customer(models.Model):
    full_name = models.CharField(max_length=255, verbose_name="Nombre Completo")
    dni_cuit = models.CharField(max_length=20, unique=True, null=True, blank=True, verbose_name="DNI / CUIT")
    phone = models.CharField(max_length=20, null=True, blank=True, verbose_name="Teléfono")
    email = models.EmailField(null=True, blank=True, verbose_name="Email")
    address = models.CharField(max_length=255, null=True, blank=True, verbose_name="Dirección")
    balance = models.DecimalField(max_digits=10, decimal_places=2, default=0, verbose_name="Saldo Deudor")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Fecha de Registro")

    def __str__(self):
        return self.full_name

    class Meta:
        verbose_name = "Cliente"
        verbose_name_plural = "Clientes"
        ordering = ['full_name']
