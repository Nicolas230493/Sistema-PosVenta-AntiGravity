from django.db import models

class Supplier(models.Model):
    name = models.CharField(max_length=100, verbose_name="Nombre")
    phone = models.CharField(max_length=20, verbose_name="Teléfono", blank=True, null=True)
    email = models.EmailField(verbose_name="Email", blank=True, null=True)
    address = models.TextField(verbose_name="Dirección", blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Fecha de registro")

    def __str__(self):
        return self.name

    class Meta:
        verbose_name = "Proveedor"
        verbose_name_plural = "Proveedores"
        ordering = ['-created_at']
