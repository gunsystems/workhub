"""Server-authoritative idle timeout; background requests never count as activity."""
from time import time
from django.contrib.auth import logout
from django.http import JsonResponse
from django.shortcuts import redirect
from django.views.decorators.http import require_POST, require_GET

TIMEOUT = 30 * 60
KEY = 'workhub_last_activity'


def mark_activity(request):
    request.session[KEY] = time()
    # Keep the DB session available for timeout handling without making automatic
    # background requests renew the authoritative activity timestamp.
    request.session.set_expiry(TIMEOUT + 300)


class IdleTimeout:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.user.is_authenticated:
            last = request.session.get(KEY)
            if last is None:
                mark_activity(request)  # Existing pre-release sessions get one new window.
            elif time() - float(last) >= TIMEOUT:
                from tasks.audit import write
                write('session_timeout','Account',request.user.pk,'30분 무활동 로그아웃', actor=request.user)
                logout(request)
                if request.path.startswith('/accounts/session/'):
                    return JsonResponse({'expired':True, 'login_url':'/login/?expired=1'}, status=401)
                return redirect('/login/?expired=1')
            elif not request.path.startswith('/accounts/session/') and (
                    request.method in ['POST','PUT','PATCH','DELETE'] or
                    (request.method == 'GET' and request.headers.get('Sec-Fetch-Mode') == 'navigate')):
                mark_activity(request)
        response = self.get_response(request)
        if getattr(request,'user',None) and request.user.is_authenticated:
            response['Cache-Control']='no-store'
        return response


@require_POST
def activity(request):
    if not request.user.is_authenticated:
        return JsonResponse({'expired':True},status=401)
    mark_activity(request)
    response=JsonResponse({'expires_at':request.session[KEY]+TIMEOUT})
    response['Cache-Control']='no-store'
    return response


@require_GET
def status(request):
    if not request.user.is_authenticated:
        return JsonResponse({'expired':True},status=401)
    response=JsonResponse({'expires_at':request.session.get(KEY,time())+TIMEOUT})
    response['Cache-Control']='no-store'
    return response
