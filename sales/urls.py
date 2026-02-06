from django.urls import path
from . import views

urlpatterns = [
    path('', views.pos_view, name='pos'),
    path('history/', views.sale_list, name='sale_list'),
]
