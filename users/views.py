from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.models import User
from django.contrib.auth.decorators import user_passes_test
from django.contrib import messages
from .forms import UserForm, UserPasswordForm

def is_superuser(user):
    return user.is_superuser

@user_passes_test(is_superuser, login_url='dashboard')
def user_list(request):
    users = User.objects.all().order_by('-date_joined')
    return render(request, 'users/user_list.html', {'users': users})

@user_passes_test(is_superuser, login_url='dashboard')
def user_create(request):
    if request.method == 'POST':
        form = UserForm(request.POST)
        if form.is_valid():
            user = form.save()
            messages.success(request, f"Usuario {user.username} creado correctamente.")
            return redirect('users:user_list')
    else:
        form = UserForm()
    return render(request, 'users/user_form.html', {'form': form, 'title': 'Crear Usuario'})

@user_passes_test(is_superuser, login_url='dashboard')
def user_edit(request, pk):
    user = get_object_or_404(User, pk=pk)
    if request.method == 'POST':
        form = UserForm(request.POST, instance=user)
        if form.is_valid():
            form.save()
            messages.success(request, f"Usuario {user.username} actualizado.")
            return redirect('users:user_list')
    else:
        form = UserForm(instance=user)
    return render(request, 'users/user_form.html', {'form': form, 'title': 'Editar Usuario'})

@user_passes_test(is_superuser, login_url='dashboard')
def user_change_password(request, pk):
    user = get_object_or_404(User, pk=pk)
    if request.method == 'POST':
        form = UserPasswordForm(request.POST, instance=user)
        if form.is_valid():
            form.save()
            messages.success(request, f"Contraseña de {user.username} actualizada.")
            return redirect('users:user_list')
    else:
        form = UserPasswordForm(instance=user)
    return render(request, 'users/user_password_form.html', {'form': form, 'user': user})

@user_passes_test(is_superuser, login_url='dashboard')
def user_delete(request, pk):
    user = get_object_or_404(User, pk=pk)
    if request.method == 'POST':
        user.delete()
        messages.success(request, "Usuario eliminado.")
        return redirect('users:user_list')
    return render(request, 'users/user_confirm_delete.html', {'user': user})
