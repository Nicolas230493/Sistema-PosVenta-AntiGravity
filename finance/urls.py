from django.urls import path
from . import views

urlpatterns = [
    path('', views.cash_dashboard, name='cash_dashboard'),
]
