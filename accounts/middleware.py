from django.shortcuts import redirect
from django_otp import user_has_device


class RequireMFA:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.user.is_authenticated:
            access = getattr(request.user, 'workhub_access', None)
            enrolled = user_has_device(request.user)
            if access and not access.verified_at:
                from django.contrib.auth import logout
                logout(request)
                return redirect('login')
            if not request.path.startswith(('/accounts/', '/logout/', '/static/', '/healthz/')):
                if access and access.require_mfa and not enrolled:
                    return redirect('mfa_setup')
                if enrolled and not request.user.is_verified():
                    return redirect('mfa_verify')
        response = self.get_response(request)
        if request.path.startswith(('/accounts/', '/login/')):
            response['Cache-Control'] = 'no-store'
            # Keep same-origin form provenance for HTTPS CSRF checks while
            # preventing activation/reset URLs from leaking to other sites.
            response['Referrer-Policy'] = 'same-origin'
        return response
