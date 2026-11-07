import os
from django.http import JsonResponse
from django.db import DatabaseError
from tasks.models import Task, Project, WorkRequest
from accounts.models import AccessProfile, Invitation


def health(request):
    data = {'version':'0.4','commit':os.getenv('RENDER_GIT_COMMIT','local')[:7]}
    try:
        Task.objects.first()
        Project.objects.first()
        WorkRequest.objects.first()
        AccessProfile.objects.first()
        Invitation.objects.first()
    except DatabaseError:
        return JsonResponse({**data,'status':'database_not_ready'},status=503)
    return JsonResponse({**data,'status':'ok'})
