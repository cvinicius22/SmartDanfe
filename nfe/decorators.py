from django.shortcuts import redirect
from .models import Payment, UserProfile

def subscription_required(view_func):
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect('login')
        has_pending = Payment.objects.filter(user=request.user, status='PENDING').exists()
        if has_pending:
            return redirect('payment_history')
        try:
            if not request.user.profile.subscription_active:
                return redirect('home')
        except UserProfile.DoesNotExist:
            return redirect('home')
        return view_func(request, *args, **kwargs)
    return wrapper