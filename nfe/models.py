from django.db import models
from django.contrib.auth.models import User
from django.db.models.signals import post_save
from django.dispatch import receiver


class NFe(models.Model):
    STATUS_CHOICES = [
        ('WAITING', 'Aguardando'),
        ('PROCESSING', 'Processando'),
        ('OK', 'Pronto'),
        ('ERROR', 'Erro'),
    ]

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='nfes')
    chave_acesso = models.CharField(max_length=44, unique=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='WAITING')
    pdf_base64 = models.TextField(blank=True, null=True)
    xml_text = models.TextField(blank=True, null=True)
    tipo = models.CharField(max_length=10, blank=True, null=True)
    mensagem = models.CharField(max_length=255, blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.chave_acesso


class Payment(models.Model):
    STATUS_CHOICES = [
        ('PENDING', 'Pendente'),
        ('APPROVED', 'Aprovado'),
        ('REJECTED', 'Rejeitado'),
        ('CANCELLED', 'Cancelado'),
        ('REFUNDED', 'Reembolsado'),
    ]
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='payments')
    plan = models.CharField(max_length=50)  # mensal, trimestral, anual
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    preference_id = models.CharField(max_length=100, blank=True, null=True)
    payment_id = models.CharField(max_length=100, blank=True, null=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='PENDING')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    init_point = models.URLField(max_length=500, blank=True, null=True)
    external_reference = models.CharField(max_length=100, blank=True, null=True)

    def __str__(self):
        return f"{self.user.username} - {self.plan} - {self.status}"


class UserProfile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='profile')
    subscription_active = models.BooleanField(default=False)
    subscription_until = models.DateTimeField(null=True, blank=True)
    plan = models.CharField(max_length=50, blank=True, null=True)
    phone = models.CharField(max_length=20, blank=True, null=True)

    def __str__(self):
        return f"{self.user.username} - {'Ativa' if self.subscription_active else 'Inativa'}"


# Sinais para criar perfil automaticamente
@receiver(post_save, sender=User)
def create_user_profile(sender, instance, created, **kwargs):
    if created:
        UserProfile.objects.get_or_create(user=instance)


@receiver(post_save, sender=User)
def save_user_profile(sender, instance, **kwargs):
    if hasattr(instance, 'profile'):
        instance.profile.save()

class Plan(models.Model):
    PLAN_TYPES = [
        ('mensal', 'Mensal'),
        ('trimestral', 'Trimestral'),
        ('anual', 'Anual'),
    ]
    name = models.CharField(max_length=20, choices=PLAN_TYPES, unique=True)
    price = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.get_name_display()} - R$ {self.price}"

    class Meta:
        verbose_name = "Plano"
        verbose_name_plural = "Planos"
