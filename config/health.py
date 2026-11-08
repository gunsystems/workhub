import os
from django.http import JsonResponse
from django.db import DatabaseError
from tasks.models import Task, Project, WorkRequest, ApprovalStep, PersonalCard,WorkNotification
from accounts.models import AccessProfile, Invitation
from tasks.models import HiworksDirectoryCache


def health(request):
    data = {'version':'0.5','commit':os.getenv('RENDER_GIT_COMMIT','local')[:7]}
    try:
        Task.objects.first()
        Project.objects.first()
        WorkRequest.objects.first()
        AccessProfile.objects.first()
        Invitation.objects.first()
        ApprovalStep.objects.first()
        PersonalCard.objects.first()
        WorkNotification.objects.first()
        HiworksDirectoryCache.objects.first()
    except DatabaseError:
        return JsonResponse({**data,'status':'database_not_ready'},status=503)
    return JsonResponse({**data,'status':'ok'})
