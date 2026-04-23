from django.urls import path
from . import views

app_name = 'products'

urlpatterns = [
    path('', views.product_list, name='product_list'),
    path('new/', views.product_create, name='product_create'),
    path('<int:pk>/edit/', views.product_update, name='product_update'),
    path('<int:pk>/delete/', views.product_delete, name='product_delete'),
    path('inventory/history/', views.inventory_history, name='inventory_history'),
    path('export/excel/', views.export_inventory_excel, name='export_inventory_excel'),
    path('import/excel/', views.import_inventory_excel, name='import_inventory_excel'),
    path('stock-entry/', views.stock_entry, name='stock_entry'),
]
