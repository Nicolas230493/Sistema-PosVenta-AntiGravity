from .models import ConfiguracionSistema

def global_config_processor(request):
    return {'config': ConfiguracionSistema.get_config()}
