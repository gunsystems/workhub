"""Leave notification outbox, disabled during administrator-only preview."""
from django.conf import settings
from django.urls import reverse
from django.utils import timezone
from django.template.loader import render_to_string
from .models import LeaveNotice


def enabled():return getattr(settings,'WORKHUB_LEAVE_NOTIFICATIONS_ENABLED',False)


def enqueue(event):
    if not enabled():return
    from .leave import current
    item=event.request
    step=current(item)
    recipients=[]
    if step and event.action in ('휴가 신청','승인','취소 신청') and step.approver.user_id:
        message='휴가 취소 신청 결재가 필요합니다.' if item.state=='cancel_pending' else '휴가 신청 결재가 필요합니다.'
        recipients.append((step.approver.user,'pending',message))
    if item.employee.user_id and (event.action=='반려' or (event.action=='승인' and item.state in ('approved','cancelled'))):
        message=('휴가 취소 신청이 반려되었습니다.' if item.state=='approved' else '휴가 신청이 반려되었습니다.') if event.action=='반려' else ('휴가 취소가 승인되었습니다.' if item.state=='cancelled' else '휴가 신청이 최종 승인되었습니다.')
        recipients.append((item.employee.user,'result',message))
    for user,kind,message in recipients:
        if not user.is_active:continue
        LeaveNotice.objects.get_or_create(event=event,user=user,kind=kind,defaults={'message':message,'snapshot':{
          'name':item.employee.name,'department':str(item.employee.department),'start':str(item.start),'end':str(item.end),
          'kind':item.get_kind_display(),'days':str(item.days),'revision':item.revision,'email':user.email.lower()}})


def actionable(row):
    from .leave import current
    item=row.event.request;step=current(item)
    return bool(row.kind=='pending' and step and step.approver.user_id==row.user_id and item.revision==row.snapshot.get('revision'))


def deliver_pending(now=None):
    if not enabled():return
    from accounts.lifecycle import account_allowed
    from accounts.services import deliver
    from .leave import visible
    now=now or timezone.now()
    for row in LeaveNotice.objects.filter(mail_state='pending').select_related('user','event__request__employee')[:50]:
        if not LeaveNotice.objects.filter(pk=row.pk,mail_state='pending').update(mail_state='sending',attempted_at=now):continue
        user=row.user;access=getattr(user,'workhub_access',None)
        valid=(user.is_active and access and access.verified_at and user.email and
          user.email.lower()==access.email.lower()==row.snapshot.get('email') and account_allowed(user) and
          visible(user).filter(pk=row.event.request_id).exists())
        if not valid or (row.kind=='pending' and not actionable(row)):
            LeaveNotice.objects.filter(pk=row.pk).update(mail_state='skipped');continue
        url=settings.WORKHUB_BASE_URL.rstrip('/')+reverse('leave_detail',args=[row.event.request_id])
        context={'notice':row,'url':url,'data':row.snapshot}
        try:
            deliver('[Workhub 전자결재] '+row.message,row.message+'\n'+row.snapshot['name']+' · '+row.snapshot['kind']+'\n'+row.snapshot['start']+' ~ '+row.snapshot['end']+'\n'+url,user.email,html_body=render_to_string('tasks/email_leave.html',context))
        except Exception:state='unknown'
        else:state='sent'
        LeaveNotice.objects.filter(pk=row.pk).update(mail_state=state)
        from .audit import write
        write('mail_'+state,'LeaveNotice',row.pk,'전자결재 메일',after={'알림 번호':row.pk,'발송 상태':state})
