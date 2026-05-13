from django.urls import path
from . import views

app_name = 'customers'

urlpatterns = [
    path('', views.customer_list, name='customer_list'),
    path('new/', views.customer_create, name='customer_create'),
    path('<int:pk>/edit/', views.customer_update, name='customer_update'),
    path('<int:pk>/delete/', views.customer_delete, name='customer_delete'),
    path('<int:pk>/payment/', views.customer_payment, name='customer_payment'),
    path('<int:pk>/reminder/', views.whatsapp_reminder, name='whatsapp_reminder'),
    path('<int:pk>/statement/pdf/', views.export_statement_pdf, name='export_statement_pdf'),
    path('<int:pk>/detail/', views.customer_detail, name='customer_detail'),
]
