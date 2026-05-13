from django.shortcuts import redirect
from django.contrib import messages
from django.urls import resolve

class RoleAccessMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.user.is_authenticated and not request.user.is_superuser:
            # Rutas restringidas para Cajeros
            restricted_for_cajeros = [
                'finance:cash_dashboard',
                'products:business_intelligence',
                'products:admin_tools',
                'products:supplier_ranking',
                'products:product_delete',
                'products:category_delete',
                'sales:sale_return', # Solo Supervisores/Admin pueden anular/devolver
            ]
            
            # Rutas restringidas para Supervisores (Encargados)
            restricted_for_supervisors = [
                'products:business_intelligence', # Solo Admin ve utilidades brutas
                'products:admin_tools', # Solo Admin descarga backups/actualización masiva
            ]

            current_url_name = f"{resolve(request.path_info).app_name}:{resolve(request.path_info).url_name}"
            
            is_cajero = request.user.groups.filter(name='Cajeros').exists()
            is_supervisor = request.user.groups.filter(name='Encargados').exists()

            if is_cajero and current_url_name in restricted_for_cajeros:
                messages.error(request, "Acceso Denegado: Su rol de Cajero no permite esta acción.")
                return redirect('sales:pos')
                
            if is_supervisor and current_url_name in restricted_for_supervisors:
                messages.error(request, "Acceso Denegado: Solo el Administrador puede ver métricas de rentabilidad.")
                return redirect('core:dashboard')

        response = self.get_response(request)
        return response
