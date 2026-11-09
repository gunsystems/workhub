from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect
from django.views.decorators.http import require_http_methods
from .hiworks_access import refresh_directory

@login_required
@require_http_methods(['GET','POST'])
def retry(request):
    if request.method=='GET':
        # Error-page recovery links use GET; never perform a mutation on that GET.
        return redirect('dashboard')
    refresh_directory(force=True)
    return redirect('dashboard')
