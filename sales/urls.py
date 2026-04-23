from django.urls import path
from . import views

app_name = 'sales'

urlpatterns = [
    path('', views.pos_view, name='pos'),
    path('history/', views.sale_list, name='sale_list'),
    path('<int:pk>/pdf/', views.export_sale_pdf, name='export_sale_pdf'),
    path('<int:pk>/ticket/', views.export_thermal_ticket, name='export_thermal_ticket'),
    path('report/consolidated/', views.export_consolidated_report, name='export_consolidated_report'),
]
