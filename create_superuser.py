import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'meudanfe_project.settings')
django.setup()

from django.contrib.auth import get_user_model
User = get_user_model()

username = os.environ.get('SUPERUSER_USERNAME', 'admin')
email = os.environ.get('SUPERUSER_EMAIL', 'admin@smartdanfe.com')
password = os.environ.get('SUPERUSER_PASSWORD')

if password and not User.objects.filter(username=username).exists():
    User.objects.create_superuser(username=username, email=email, password=password)
    print(f"Superusuário '{username}' criado.")
else:
    print("Superusuário já existe ou senha não definida.")
