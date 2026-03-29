from django.shortcuts import redirect
from django.utils import timezone
from .models import Payment, UserProfile

def subscription_required(view_func):
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect('login')
        
        # Verifica se há pagamento pendente
        has_pending = Payment.objects.filter(user=request.user, status='PENDING').exists()
        if has_pending:
            return redirect('pending_payments')
        
        # Verifica assinatura ativa e data de expiração
        try:
            profile = request.user.profile
            if not profile.subscription_active:
                return redirect('home')
            
            # Se a assinatura expirou, desativa e redireciona
            if profile.subscription_until and profile.subscription_until <= timezone.now():
                profile.subscription_active = False
                profile.save()
                return redirect('home')
                
        except UserProfile.DoesNotExist:
            return redirect('home')
        
        return view_func(request, *args, **kwargs)
    return wrapper
