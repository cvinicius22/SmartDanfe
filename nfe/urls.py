from django.urls import path
from . import views

urlpatterns = [
    path('', views.dashboard, name='dashboard'),
    path('api/process-keys/', views.process_keys, name='process_keys'),
    path('api/nfe-status/', views.nfe_status, name='nfe_status'),
    path('api/download-pdf/<str:chave>/', views.download_pdf, name='download_pdf'),
    path('api/download-xml/<str:chave>/', views.download_xml, name='download_xml'),
    path('api/relatorio-excel/', views.relatorio_excel, name='relatorio_excel'),
    path('api/clear-all/', views.clear_all, name='clear_all'),
    path('api/stats/', views.stats, name='stats'),
    path('checkout/', views.checkout, name='checkout'),
    path('process-payment/', views.process_payment, name='process_payment'),
    path('pending-payments/', views.pending_payments, name='pending_payments'),
    path('payment/success/', views.payment_success, name='payment_success'),
    path('payment/failure/', views.payment_failure, name='payment_failure'),
    path('payment/pending/', views.payment_pending, name='payment_pending'),
    path('payment/webhook/', views.payment_webhook, name='payment_webhook'),
    path('payment-history/', views.payment_history, name='payment_history'),
]