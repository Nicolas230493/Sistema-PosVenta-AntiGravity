from django.urls import path
from . import views

app_name = 'suppliers'

urlpatterns = [
    path('', views.supplier_list, name='supplier_list'),
    path('new/', views.supplier_create, name='supplier_create'),
    path('remito/', views.supplier_remito_entry, name='supplier_remito_entry'),
    path('<int:pk>/remito/', views.supplier_remito_entry, name='supplier_remito_entry_pk'),
    path('<int:pk>/edit/', views.supplier_update, name='supplier_update'),
    path('<int:pk>/account/', views.supplier_account, name='supplier_account'),
    path('<int:pk>/account/pay/', views.supplier_payment, name='supplier_payment'),
    path('<int:pk>/delete/', views.supplier_delete, name='supplier_delete'),
]
