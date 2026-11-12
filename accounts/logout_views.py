"""CSRF-protected logout with a safe, non-mutating confirmation GET."""
from django.contrib.auth.views import LogoutView
from django.shortcuts import redirect
from django.views.decorators.cache import never_cache


@never_cache
def confirmation(request, status=200):
    if not request.user.is_authenticated:
        return redirect('login')
    from .views import page
    response = page(request, '로그아웃 확인', logout_confirmation=True,
                    notice='현재 계정에서 로그아웃하시겠습니까?')
    response.status_code = status
    response['Referrer-Policy'] = 'same-origin'
    return response


class SafeLogoutView(LogoutView):
    http_method_names = ['get', 'post', 'options']

    def get(self, request, *args, **kwargs):
        return confirmation(request)
