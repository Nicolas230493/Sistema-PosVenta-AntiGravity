from django.urls import path
from . import views

app_name = 'products'

urlpatterns = [
    path('', views.product_list, name='product_list'),
    path('new/', views.product_create, name='product_create'),
    path('<int:pk>/edit/', views.product_update, name='product_update'),
    path('<int:pk>/delete/', views.product_delete, name='product_delete'),
    # Categorías
    path('categories/', views.category_list, name='category_list'),
    path('categories/new/', views.category_create, name='category_create'),
    path('categories/<int:pk>/edit/', views.category_update, name='category_update'),
    path('categories/<int:pk>/delete/', views.category_delete, name='category_delete'),
    path('inventory/history/', views.inventory_history, name='inventory_history'),
    path('export/excel/', views.export_inventory_excel, name='export_inventory_excel'),
    path('export/excel-advanced/', views.export_advanced_excel, name='export_advanced_excel'),
    path('import/excel/', views.import_inventory_excel, name='import_inventory_excel'),
    path('stock-entry/', views.stock_entry_scanner, name='stock_entry_scanner'),
    path('purchases/', views.purchase_list, name='purchase_list'),
    path('purchases/new/', views.purchase_create, name='purchase_create'),
    path('admin-tools/', views.bulk_price_update, name='admin_tools'),
    path('admin-tools/backup/', views.download_backup, name='download_backup'),
    path('stock-loss/', views.stock_loss_create, name='stock_loss_create'),
    path('bi/', views.business_intelligence, name='bi_dashboard'),
    path('orders/', views.order_assistant, name='order_assistant'),
    path('purchase-orders/', views.purchase_order_list, name='purchase_order_list'),
    path('purchase-orders/<int:pk>/status/', views.update_purchase_order_status, name='update_purchase_order_status'),
    path('export/labels/', views.export_labels, name='export_labels'),
    path('supplier-ranking/', views.supplier_ranking, name='supplier_ranking'),
]
