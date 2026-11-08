from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect
from django.views.decorators.http import require_POST
from .hiworks_access import refresh_directory

@login_required
@require_POST
def retry(request):
    refresh_directory(force=True)
    return redirect('dashboard')
