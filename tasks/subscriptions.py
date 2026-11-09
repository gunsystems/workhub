"""Owner-scoped iCalendar subscriptions. URL tokens are never stored in plaintext."""
import hashlib,secrets
from datetime import datetime,time,timedelta, timezone as dt_timezone
from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import HttpResponse, Http404
from django.shortcuts import render,redirect
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_safe
from django.db.models import Q,Count,Max
from .models import CalendarSubscription,CalendarExportPolicy
from .permissions import visible_tasks,profile,is_company_admin


@login_required
@never_cache
def settings_view(request):
    if not is_company_admin(request.user) and not profile(request.user): raise Http404
    allowed=CalendarExportPolicy.objects.filter(pk=1,allow_official=True).exists()
    if request.method=='POST':
        if request.POST.get('action')=='revoke':
            CalendarSubscription.objects.filter(owner=request.user).update(enabled=False)
            request.session.pop('calendar_subscription_url',None)
            messages.success(request,'구독 주소를 폐기했습니다. 외부에 이미 표시된 내용은 즉시 회수되지 않을 수 있습니다.')
        else:
            if not allowed:
                messages.error(request,'관리자의 회사 업무 외부 캘린더 전송 허용이 필요합니다.')
                return redirect('calendar_subscription')
            token=secrets.token_urlsafe(32)
            CalendarSubscription.objects.update_or_create(owner=request.user,defaults={'token_digest':hashlib.sha256(token.encode()).hexdigest(),'enabled':True,'created_at':timezone.now()})
            request.session['calendar_subscription_url']=settings.WORKHUB_BASE_URL+'/work/calendar/subscription/'+token+'.ics'
        return redirect('calendar_subscription')
    url=request.session.pop('calendar_subscription_url','')
    response=render(request,'tasks/calendar_subscription.html',{'subscription':CalendarSubscription.objects.filter(owner=request.user).first(),'subscription_url':url,'export_allowed':allowed})
    # This HTML page contains CSRF-protected forms, including the shared logout.
    # Retain same-origin provenance while hiding referrers from external sites.
    response['Referrer-Policy']='same-origin'
    return response


def escape(value): return str(value).replace('\\','\\\\').replace('\r','').replace('\n','\\n').replace(';','\\;').replace(',','\\,')


def fold(line):
    # RFC 5545 limit is octets, not unicode characters.
    result=[];part='';size=0
    for char in line:
        n=len(char.encode('utf-8'))
        if size+n>73:result.append(part);part=' ';size=1
        part+=char;size+=n
    result.append(part)
    return '\r\n'.join(result)


@require_safe
@never_cache
def feed(request,token):
    if len(token)!=43:raise Http404
    subscription=CalendarSubscription.objects.select_related('owner').filter(enabled=True,token_digest=hashlib.sha256(token.encode()).hexdigest()).first()
    from accounts.lifecycle import account_allowed
    if not subscription or not account_allowed(subscription.owner):raise Http404
    if not CalendarExportPolicy.objects.filter(pk=1,allow_official=True).exists():raise Http404
    access=getattr(subscription.owner,'workhub_access',None)
    if access and access.otp_reset_key:raise Http404
    owner=subscription.owner
    today=timezone.localdate()
    tasks=visible_tasks(owner).filter(Q(assignee__user=owner)|Q(participants__user=owner),due_date__gte=today-timedelta(days=90),start_date__lte=today+timedelta(days=366)).exclude(status='draft').annotate(calendar_event_count=Count('events',distinct=True),calendar_modified=Max('events__created_at')).distinct()
    lines=['BEGIN:VCALENDAR','VERSION:2.0','PRODID:-//GUNSYSTEMS//Workhub//KO','CALSCALE:GREGORIAN','METHOD:PUBLISH','X-WR-CALNAME:Workhub 내 업무','X-WR-TIMEZONE:Asia/Seoul']
    stamp=timezone.now().astimezone(dt_timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    for task in tasks[:2000]:
        # Legacy edits recorded immutable events without advancing revision.
        # Include that history so existing edits are exported without rewriting
        # business data. Distinct prevents participant joins inflating the count.
        sequence=task.revision+task.calendar_event_count
        modified=(task.calendar_modified or task.assigned_at or datetime.combine(task.start_date,time(),tzinfo=dt_timezone.utc)).astimezone(dt_timezone.utc).strftime('%Y%m%dT%H%M%SZ')
        lines+=['BEGIN:VEVENT',f'UID:workhub-task-{task.pk}@gunsystems.co.kr',f'DTSTAMP:{stamp}',f'LAST-MODIFIED:{modified}',f'SEQUENCE:{sequence}', 'SUMMARY:'+escape(task.title)]
        if task.activity_start and task.activity_end:
            lines+=['DTSTART:'+task.activity_start.astimezone(dt_timezone.utc).strftime('%Y%m%dT%H%M%SZ'),'DTEND:'+task.activity_end.astimezone(dt_timezone.utc).strftime('%Y%m%dT%H%M%SZ')]
        else:lines+=['DTSTART;VALUE=DATE:'+task.start_date.strftime('%Y%m%d'),'DTEND;VALUE=DATE:'+(task.due_date+timedelta(days=1)).strftime('%Y%m%d')]
        lines+=['STATUS:'+('CANCELLED' if task.status=='cancelled' else 'CONFIRMED'),'URL:'+settings.WORKHUB_BASE_URL+f'/tasks/{task.pk}/','CLASS:PRIVATE','END:VEVENT']
    lines+=['END:VCALENDAR']
    response=HttpResponse('\r\n'.join(fold(line) for line in lines)+'\r\n',content_type='text/calendar; charset=utf-8')
    response['Content-Disposition']='attachment; filename="workhub.ics"'
    response['Referrer-Policy']='no-referrer'
    response['X-Robots-Tag']='noindex, nofollow'
    return response
