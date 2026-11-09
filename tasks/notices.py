"""Confirmed audiences, durable schedules and one-attempt channel deliveries."""
from datetime import timedelta
from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from .models import Notice,NoticeRecipient,NoticeDelivery,Employee,HiworksLink
from .permissions import is_company_admin
from .audit import write


def targets(notice):
    employees=Employee.objects.filter(is_active=True,user__is_active=True,
        user__workhub_access__verified_at__isnull=False).select_related('user__workhub_access','department')
    if notice.audience=='team':employees=employees.filter(department__in=notice.departments.all())
    elif notice.audience=='person':employees=employees.filter(pk__in=notice.employees.all())
    return employees.order_by('department__name','name').distinct()


@transaction.atomic
def queue(notice_id,user):
    if not is_company_admin(user):raise PermissionDenied
    notice=Notice.objects.select_for_update().get(pk=notice_id)
    if notice.state!='draft':raise ValidationError('이미 발송을 확정했거나 취소한 공지입니다.')
    if notice.timing=='scheduled' and notice.scheduled_for<=timezone.now():
        raise ValidationError('예약 시각이 지났습니다. 발송 날짜·시간을 수정해 주세요.')
    audience=list(targets(notice))
    if not audience:raise ValidationError('초대·이메일 인증을 마친 전달 대상 직원이 없습니다.')
    if not notice.send_email and not notice.send_messenger:raise ValidationError('전달 방법을 하나 이상 선택하세요.')
    for employee in audience:
        recipient=NoticeRecipient.objects.create(notice=notice,employee=employee,user=employee.user,
            employee_name=employee.name,email=employee.user.workhub_access.email)
        channels=(['email'] if notice.send_email else [])+(['messenger'] if notice.send_messenger else [])
        NoticeDelivery.objects.bulk_create([NoticeDelivery(recipient=recipient,channel=c) for c in channels])
    notice.state='scheduled';notice.save(update_fields=['state'])
    write('notice_scheduled','Notice',notice.pk,notice.title,after={'recipient_count':len(audience),
          '발송 시각':timezone.localtime(notice.scheduled_for).strftime('%Y-%m-%d %H:%M'),
          '이메일':notice.send_email,'하이웍스 메신저':notice.send_messenger},actor=user)
    return notice


@transaction.atomic
def cancel(notice_id,user):
    if not is_company_admin(user):raise PermissionDenied
    notice=Notice.objects.select_for_update().get(pk=notice_id)
    if notice.state not in ['draft','scheduled']:raise ValidationError('이미 게시·발송을 시작한 공지는 취소할 수 없습니다.')
    if not Notice.objects.filter(pk=notice.pk,state=notice.state).update(state='cancelled'):
        raise ValidationError('처리 상태가 변경됐습니다. 다시 확인하세요.')
    NoticeDelivery.objects.filter(recipient__notice=notice,state='pending').update(state='cancelled')
    write('notice_cancelled','Notice',notice.pk,notice.title,actor=user)


def publish_due(now=None):
    now=now or timezone.now()
    for pk in Notice.objects.filter(state='scheduled',scheduled_for__lte=now).values_list('pk',flat=True):
        if Notice.objects.filter(pk=pk,state='scheduled',scheduled_for__lte=now).update(state='published',published_at=now):
            write('notice_published','Notice',pk,'공지 게시',after={'status':'직원 공지함에 게시했습니다.'})


def deliver_pending(limit=5):
    from accounts.hiworks_access import fresh_cache
    from accounts.lifecycle import account_allowed
    from accounts.services import deliver
    from .hiworks import api
    # Missing authoritative directory information defers delivery, never bypasses access.
    if settings.WORKHUB_REQUIRE_HIWORKS and not fresh_cache():return
    now=timezone.now()
    NoticeDelivery.objects.filter(state='sending',claimed_until__lt=now).update(
        state='unknown',result='처리가 중단되어 발송 결과 확인이 필요합니다. 자동 재발송하지 않습니다.')
    jobs=list(NoticeDelivery.objects.filter(state='pending',recipient__notice__state='published')
        .select_related('recipient__notice','recipient__employee__user__workhub_access').order_by('pk')[:limit])
    for job in jobs:
        if not NoticeDelivery.objects.filter(pk=job.pk,state='pending').update(state='sending',
                claimed_until=now+timedelta(minutes=2),attempted_at=now):continue
        recipient=job.recipient;notice=recipient.notice;employee=recipient.employee
        user=employee.user if employee and employee.user_id else None
        access=getattr(user,'workhub_access',None) if user else None
        if not (employee and employee.is_active and user and user.pk==recipient.user_id and access and
                access.verified_at and access.email.lower()==recipient.email.lower() and account_allowed(user)):
            job.state='skipped';job.result='현재 재직·계정·이메일 인증 상태를 확인할 수 없어 발송하지 않았습니다.'
        else:
            url=settings.WORKHUB_BASE_URL.rstrip('/')+reverse('notice_detail',args=[notice.pk])
            try:
                if job.channel=='email':
                    context={'notice':notice,'employee_name':recipient.employee_name,'notice_url':url}
                    deliver('[Workhub 공지] '+notice.title,
                        f'{recipient.employee_name}님, 회사 공지가 도착했습니다.\n{notice.title}\n\n{notice.body}\n\n공지 확인: {url}',
                        recipient.email,html_body=render_to_string('tasks/email_notice.html',context))
                else:
                    link=HiworksLink.objects.filter(employee=employee).first()
                    if not link:raise ValidationError('하이웍스 직원 연결을 확인하세요.')
                    api('/office/v2/notify',settings.HIWORKS_NOTIFY_TOKEN,{'user_list':[link.user_id],
                        'message':'[Workhub 공지] '+notice.title,'link':url,'solution_name':'GUNSYSTEMS Workhub'})
            except Exception:
                job.state='unknown';job.result='발송 결과를 확인하지 못했습니다. 중복 방지를 위해 자동 재발송하지 않습니다.'
            else:
                job.state='sent';job.result='발송 서비스가 요청을 접수했습니다.'
        job.save(update_fields=['state','result'])
        write('notice_delivery','NoticeDelivery',job.pk,notice.title,
              after={'employee_name':recipient.employee_name,'channel':job.get_channel_display(),'status':job.get_state_display(),'result':job.result})
