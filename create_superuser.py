import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'meudanfe_project.settings')
django.setup()

from django.contrib.auth import get_user_model
User = get_user_model()

username = os.environ.get('SUPERUSER_USERNAME')
email = os.environ.get('SUPERUSER_EMAIL')
password = os.environ.get('SUPERUSER_PASSWORD')

if username and password:
    if not User.objects.filter(username=username).exists():
        User.objects.create_superuser(username=username, email=email, password=password)
        print(f"Superusuário '{username}' criado com sucesso.")
    else:
        print(f"Superusuário '{username}' já existe.")
else:
    print("Variáveis de ambiente para superusuário não configuradas.")
