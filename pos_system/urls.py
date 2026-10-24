from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from sales import views as sales_views

urlpatterns = [
    path('admin/', admin.site.urls),
    path('', include('core.urls')),
    path('suppliers/', include('suppliers.urls')),
    path('products/', include('products.urls')),
    path('pos/', include('sales.urls')),
    path('api/sales/sync/', sales_views.sync_sales, name='sales_sync'),
    # SPEC-PWA-11 §11.1: Service Worker y manifiesto en la raíz (alcance '/')
    path('sw.js', sales_views.service_worker, name='service_worker_root'),
    path('manifest.json', sales_views.web_manifest, name='web_manifest'),
    path('customers/', include('customers.urls')),
    path('finance/', include('finance.urls')),
    path('users/', include('users.urls')),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
