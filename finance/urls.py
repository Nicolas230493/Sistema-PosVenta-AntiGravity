from django.urls import path
from . import views

app_name = 'finance'

urlpatterns = [
    path('', views.cash_dashboard, name='cash_dashboard'),
    path('open/', views.open_cash, name='open_cash'),
    path('expense/', views.add_expense, name='add_expense'),
    path('close/', views.close_cash, name='close_cash'),
]
