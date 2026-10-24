from functools import wraps

from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect
from django.contrib import messages

ADMIN_GROUPS = ('Administrador', 'Administradores')
MANAGER_GROUPS = ADMIN_GROUPS + ('Encargado', 'Encargados')
CASHIER_GROUPS = ('Cajero', 'Cajeros')


def es_administrador(user):
    return user.is_superuser or user.groups.filter(name__in=ADMIN_GROUPS).exists()


def es_encargado_o_admin(user):
    """Roles con acceso a anulaciones, ajustes de caja y reportes financieros."""
    return (
        user.is_superuser
        or user.is_staff
        or user.groups.filter(name__in=MANAGER_GROUPS).exists()
    )


def es_arqueo_ciego(user):
    """
    SPEC-FIN-06 §6.1 / SPEC-UI-12 §12.1: solo Superuser y grupo Administrador(es) ven el efectivo
    esperado al cerrar caja; el resto (Cajeros, Encargados) declara a ciegas.
    """
    return not es_administrador(user)


def _role_required(check, error_message):
    def decorator(view_func):
        @wraps(view_func)
        @login_required
        def wrapped(request, *args, **kwargs):
            if not check(request.user):
                messages.error(request, error_message)
                return redirect('dashboard')
            return view_func(request, *args, **kwargs)
        return wrapped
    return decorator


admin_required = _role_required(
    es_administrador, "Acceso denegado: Se requieren privilegios de administrador."
)
manager_required = _role_required(
    es_encargado_o_admin, "Acceso denegado: Se requiere rol de Encargado o Administrador."
)
