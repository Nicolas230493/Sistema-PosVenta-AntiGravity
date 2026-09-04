from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone

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

class TurnoCaja(models.Model):
    ESTADOS = [('ABIERTO', 'Abierto'), ('CERRADO', 'Cerrado')]
    usuario = models.ForeignKey(User, on_delete=models.PROTECT, verbose_name="Cajero")
    fecha_apertura = models.DateTimeField(default=timezone.now, verbose_name="Fecha Apertura")
    fecha_cierre = models.DateTimeField(null=True, blank=True, verbose_name="Fecha Cierre")
    monto_inicial = models.DecimalField(max_digits=10, decimal_places=2, verbose_name="Monto Inicial")
    monto_final = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, verbose_name="Monto Final")
    estado = models.CharField(max_length=10, choices=ESTADOS, default='ABIERTO', verbose_name="Estado")

    class Meta:
        verbose_name = "Turno de Caja"
        verbose_name_plural = "Turnos de Caja"
        ordering = ['-fecha_apertura']

    def __str__(self):
        return f"Turno {self.id} - {self.usuario.username} ({self.estado})"
