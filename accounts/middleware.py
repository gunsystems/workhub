from django.shortcuts import redirect
from django_otp import user_has_device
from django.conf import settings
from .lifecycle import local_account_allowed


class RequireMFA:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.user.is_authenticated:
            if not local_account_allowed(request.user):
                from django.contrib.auth import logout
                logout(request)
                return redirect('login')
            access = getattr(request.user, 'workhub_access', None)
            enrolled = user_has_device(request.user)
            if access and access.otp_reset_key:
                from .reset_views import recovery_proven
                allowed = ('/accounts/mfa/restore/', '/logout/')
                if not request.path.startswith(allowed) and not (request.path == '/accounts/mfa/setup/' and recovery_proven(request, access)):
                    return redirect('mfa_restore')
            if access and not access.verified_at:
                from django.contrib.auth import logout
                logout(request)
                return redirect('login')
            authentication_path = request.path.startswith(('/accounts/session/', '/accounts/mfa/', '/accounts/activate/', '/accounts/password/', '/static/')) or request.path in ('/accounts/hiworks/access/', '/logout/', '/login/', '/healthz/')
            if not authentication_path:
                from .hiworks_access import allowed
                from .hiworks_access import fresh_cache, refresh_directory
                # Refresh before blocking a verified employee when the background
                # directory refresh is delayed. This runs before any view transaction.
                if settings.WORKHUB_REQUIRE_HIWORKS and not request.user.is_superuser and not fresh_cache():
                    refresh_directory()
                if not allowed(request.user):
                    from django.shortcuts import render
                    response=render(request, 'accounts/hiworks_required.html', status=403)
                    response['Cache-Control']='no-store'
                    response['Referrer-Policy']='same-origin'
                    return response
                if access and (access.require_mfa or (settings.WORKHUB_REQUIRE_HIWORKS and not request.user.is_superuser)) and not enrolled:
                    return redirect('mfa_setup')
                if enrolled and not request.user.is_verified():
                    return redirect('mfa_verify')
        response = self.get_response(request)
        if request.path == '/work/calendar/google/callback/':
            response['Referrer-Policy']='no-referrer'
        if request.path.startswith(('/accounts/', '/login/')):
            response['Cache-Control'] = 'no-store'
            # Keep same-origin form provenance for HTTPS CSRF checks while
            # preventing activation/reset URLs from leaking to other sites.
            response['Referrer-Policy'] = 'same-origin'
        return response
