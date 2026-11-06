import os
from django.http import JsonResponse
from django.db import DatabaseError
from tasks.models import Task, Project, WorkRequest


def health(request):
    data = {'version':'0.3','commit':os.getenv('RENDER_GIT_COMMIT','local')[:7]}
    try:
        Task.objects.first()
        Project.objects.first()
        WorkRequest.objects.first()
    except DatabaseError:
        return JsonResponse({**data,'status':'database_not_ready'},status=503)
    return JsonResponse({**data,'status':'ok'})
