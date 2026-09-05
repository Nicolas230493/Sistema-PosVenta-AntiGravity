from django.urls import path
from django.contrib.auth import views as auth_views
from . import views

urlpatterns = [
    path('', views.dashboard_view, name='dashboard'),
    path('search/', views.global_search, name='global_search'),
    path('cuenta-expirada/', views.cuenta_expirada, name='cuenta_expirada'),
    path('login/', auth_views.LoginView.as_view(template_name='registration/login.html'), name='login'),
    path('logout/', auth_views.LogoutView.as_view(next_page='login'), name='logout'),
    path('configuracion/', views.configuracion_view, name='configuracion'),
]
