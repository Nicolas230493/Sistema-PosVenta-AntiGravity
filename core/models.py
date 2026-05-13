from django.db import models
from django.contrib.auth.models import User

class ActivityLog(models.Model):
    user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, verbose_name="Usuario")
    action = models.CharField(max_length=255, verbose_name="Acción")
    module = models.CharField(max_length=100, verbose_name="Módulo")
    timestamp = models.DateTimeField(auto_now_add=True, verbose_name="Fecha y Hora")
    details = models.TextField(blank=True, null=True, verbose_name="Detalles")
    ip_address = models.GenericIPAddressField(null=True, blank=True)

    class Meta:
        verbose_name = "Log de Auditoría"
        verbose_name_plural = "Logs de Auditoría"
        ordering = ['-timestamp']

    def __str__(self):
        return f"{self.user} - {self.action} ({self.timestamp})"
