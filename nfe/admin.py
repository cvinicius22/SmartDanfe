from django.contrib import admin
from .models import NFe, Payment, UserProfile, Plan

@admin.register(NFe)
class NFeAdmin(admin.ModelAdmin):
    list_display = ('chave_acesso', 'user', 'status', 'tipo', 'created_at')
    list_filter = ('status', 'tipo', 'user')
    search_fields = ('chave_acesso',)

@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = ('user', 'plan', 'amount', 'status', 'created_at')
    list_filter = ('status', 'plan', 'user')

@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = ('user', 'subscription_active', 'subscription_until', 'plan', 'phone')
    list_filter = ('subscription_active', 'plan')

@admin.register(Plan)
class PlanAdmin(admin.ModelAdmin):
    list_display = ('name', 'price', 'is_active', 'created_at')
    list_filter = ('is_active',)
    list_editable = ('price', 'is_active')
    search_fields = ('name',)
