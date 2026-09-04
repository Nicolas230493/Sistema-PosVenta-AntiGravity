from django.contrib.auth.decorators import user_passes_test
from django.shortcuts import redirect
from django.contrib import messages

def es_administrador(user):
    return user.is_superuser or user.groups.filter(name='Administrador').exists()

def admin_required(view_func):
    """Decorador para restringir el acceso a administradores."""
    decorator = user_passes_test(
        es_administrador,
        login_url='dashboard', # O la URL que corresponda al inicio/dashboard
        redirect_field_name=None
    )
    
    def wrapped(request, *args, **kwargs):
        if not es_administrador(request.user):
            messages.error(request, "Acceso denegado: Se requieren privilegios de administrador.")
            return redirect('dashboard')
        return decorator(view_func)(request, *args, **kwargs)
    
    return wrapped
