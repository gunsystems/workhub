import os
from django.http import JsonResponse
from django.db import DatabaseError
from tasks.models import Task, Project, WorkRequest, ApprovalStep, PersonalCard,WorkNotification
from accounts.models import AccessProfile, Invitation
from tasks.models import HiworksDirectoryCache
from tasks.version import VERSION


def health(request):
    data = {'version':VERSION,'commit':os.getenv('RENDER_GIT_COMMIT','local')[:7]}
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
        from tasks.models import WorkSeries,PeriodReport,CalendarSubscription,ReportFile,SystemAudit,AssignmentMail,Notice,NoticeDelivery
        WorkSeries.objects.first();PeriodReport.objects.first();CalendarSubscription.objects.first();ReportFile.objects.first()
        SystemAudit.objects.first();AssignmentMail.objects.first();Notice.objects.first();NoticeDelivery.objects.first()
        from tasks.models import OffboardingCase,OffboardingItem,OffboardingEvent,OffboardingEvidence,OffboardingNotice
        OffboardingCase.objects.first();OffboardingItem.objects.first();OffboardingEvent.objects.first();OffboardingEvidence.objects.first();OffboardingNotice.objects.first()
        from tasks.models import PublicHoliday, HolidaySync, LeaveCalendarReview
        PublicHoliday.objects.first();HolidaySync.objects.first();LeaveCalendarReview.objects.first()
        from tasks.models import StaffEmployment,CalendarQuickSave
        StaffEmployment.objects.first();CalendarQuickSave.objects.first()
    except DatabaseError:
        return JsonResponse({**data,'status':'database_not_ready'},status=503)
    return JsonResponse({**data,'status':'ok'})
