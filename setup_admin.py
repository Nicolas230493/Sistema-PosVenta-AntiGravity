import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'pos_system.settings')
django.setup()

from django.contrib.auth.models import User

try:
    if not User.objects.filter(username='admin').exists():
        password = os.environ.get('ADMIN_PASSWORD')
        if not password:
            raise RuntimeError("Set ADMIN_PASSWORD before creating the admin user.")
        User.objects.create_superuser('admin', 'admin@example.com', password)
        print("Superuser 'admin' created")
    else:
        print("Superuser 'admin' already exists")
except Exception as e:
    print(f"Error creating superuser: {e}")
