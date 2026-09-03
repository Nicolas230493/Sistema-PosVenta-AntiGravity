from django.urls import path
from . import views

app_name = 'users'

urlpatterns = [
    path('', views.user_list, name='user_list'),
    path('create/', views.user_create, name='user_create'),
    path('<int:pk>/edit/', views.user_edit, name='user_edit'),
    path('<int:pk>/password/', views.user_change_password, name='user_change_password'),
    path('<int:pk>/delete/', views.user_delete, name='user_delete'),
]
