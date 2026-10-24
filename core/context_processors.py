from .decorators import es_administrador
from .models import ConfiguracionSistema

def global_config_processor(request):
    user = getattr(request, 'user', None)
    return {
        'config': ConfiguracionSistema.get_config(),
        # Mismo criterio de rol que las vistas (Superuser o grupo Administrador/Administradores)
        'es_admin': bool(user and user.is_authenticated and es_administrador(user)),
    }
