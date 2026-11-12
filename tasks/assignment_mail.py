"""Transactional outbox. Network calls never run inside the task-save transaction."""
from datetime import timedelta
from django.conf import settings
from django.db.models import F
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from .models import AssignmentMail, Employee, Task


def enqueue_comment(event,notification):
    task=event.task
    if not task.assignment_email or task.status=='draft' or notification.user_id==event.author_id:return
    employee=Employee.objects.filter(user_id=notification.user_id,is_active=True).select_related('user').first()
    access=getattr(employee.user,'workhub_access',None) if employee else None
    if not access or not access.verified_at:return
    AssignmentMail.objects.get_or_create(dedupe_key=f'comment:{event.pk}:{notification.user_id}',defaults={'task':task,'employee':employee,
        'payload':{'kind':'comment','event_id':event.pk,'user_id':notification.user_id,'email':access.email,'task_title':task.title}})


def deliver_comment(mail):
    from accounts.lifecycle import account_allowed
    from accounts.services import deliver
    from .models import TaskEvent
    from .permissions import visible_tasks
    employee=mail.employee;user=employee.user if employee and employee.user_id else None
    access=getattr(user,'workhub_access',None) if user else None
    event=TaskEvent.objects.filter(pk=mail.payload.get('event_id'),task_id=mail.task_id).select_related('task','author').first()
    if not (event and event.task.assignment_email and event.task.status!='draft' and employee and employee.is_active and user and access and access.verified_at
            and user.pk==mail.payload.get('user_id') and access.email.lower()==str(mail.payload.get('email','')).lower()==user.email.lower()
            and account_allowed(user) and visible_tasks(user).filter(pk=event.task_id).exists()):
        mail.state='skipped';mail.result='현재 접근 권한 또는 알림 설정 변경으로 제외했습니다.'
    else:
        url=settings.WORKHUB_BASE_URL.rstrip('/')+reverse('task_detail',args=[event.task_id])
        context={'title':'업무에 새 의견이 등록되었습니다','item':event.task,'event':event,'url':url}
        try:deliver('[Workhub] 업무에 새 의견이 등록되었습니다',f'{event.task.title}\n{event.body}\n{url}',access.email,
                    html_body=render_to_string('tasks/email_comment.html',context))
        except Exception:mail.state='unknown';mail.result='발송 결과 확인 필요 · 자동 재발송하지 않습니다.'
        else:mail.state='sent';mail.sent_at=timezone.now();mail.result='메일 서버가 발송을 접수했습니다.'
    mail.save(update_fields=['state','result','sent_at'])
    from .audit import write
    write('mail_'+mail.state,'AssignmentMail',mail.pk,'업무 의견 메일',after={'task_id':mail.task_id,'event_id':mail.payload.get('event_id'),'result':mail.result})


def enqueue(task, actor, employee_ids=None):
    if not task.assignment_email or task.status in ['draft', 'cancelled', 'done']:
        return
    ids = set(employee_ids) if employee_ids is not None else set(task.participants.values_list('pk', flat=True)) | {task.assignee_id}
    for employee in Employee.objects.filter(pk__in=ids, is_active=True).select_related('user'):
        access = getattr(employee.user, 'workhub_access', None) if employee.user_id else None
        if not employee.user_id or not employee.user.is_active or not access or not access.verified_at:
            continue
        assigned_stamp=task.assigned_at.isoformat() if task.assigned_at else ''
        AssignmentMail.objects.get_or_create(dedupe_key=f'{task.pk}:{task.revision}:{assigned_stamp}:{employee.pk}', defaults={
            'task': task, 'employee': employee, 'payload': {
                'employee_name': employee.name, 'email': access.email, 'user_id': employee.user_id,
                'assigned_by': getattr(getattr(actor, 'employee', None), 'name', '') or (actor.get_username() if actor else '시스템'),
                'task_title': task.title, 'due_date': task.due_date.isoformat(),
                'assignment_role': '총괄 담당자' if employee.pk == task.assignee_id else '공동 수행자'}})


def deliver_pending(limit=10):
    from accounts.lifecycle import account_allowed
    from accounts.services import deliver
    from .audit import write
    now = timezone.now()
    # An interrupted send may already have been accepted by the provider.
    AssignmentMail.objects.filter(state='sending', claimed_until__lt=now).update(state='unknown', result='발송 도중 처리가 중단되어 결과 확인이 필요합니다.')
    ids = list(AssignmentMail.objects.filter(state='pending', next_attempt_at__lte=now).order_by('pk').values_list('pk', flat=True)[:limit])
    for pk in ids:
        if not AssignmentMail.objects.filter(pk=pk, state='pending').update(state='sending', attempts=F('attempts')+1, claimed_until=now+timedelta(minutes=2)):
            continue
        mail = AssignmentMail.objects.select_related('task', 'employee__user').get(pk=pk)
        if mail.payload.get('kind')=='comment':
            deliver_comment(mail)
            continue
        employee, task = mail.employee, mail.task
        user = employee.user if employee and employee.user_id else None
        access = getattr(user, 'workhub_access', None) if user else None
        still_assigned = task and (task.assignee_id == mail.employee_id or task.participants.filter(pk=mail.employee_id).exists())
        if not (still_assigned and task.assignment_email and task.status not in ['draft','cancelled','done'] and employee.is_active and user and
                user.pk == mail.payload.get('user_id') and access and access.verified_at and
                access.email == mail.payload.get('email') and user.email.lower() == access.email.lower() and account_allowed(user)):
            mail.state='skipped'; mail.result='현재 배정 또는 인증·재직 상태가 변경되어 발송하지 않았습니다.'
        else:
            context={**mail.payload, 'task_title':task.title, 'due_date':task.due_date,
                     'assignment_role':'총괄 담당자' if employee.pk==task.assignee_id else '공동 수행자',
                     'task_url':settings.WORKHUB_BASE_URL.rstrip('/')+reverse('task_detail', args=[task.pk])}
            body=f"{employee.name}님, 새로운 업무가 배정되었습니다.\n업무: {task.title}\n마감일: {task.due_date}\n업무 확인: {context['task_url']}\n회사 계정으로 로그인해 자세한 내용을 확인해 주세요."
            try:
                deliver('[Workhub] 새로운 업무가 배정되었습니다', body, access.email,
                        html_body=render_to_string('tasks/email_assignment.html', context))
            except Exception:
                # Do not store provider responses, tokens, addresses or exception text.
                mail.state='unknown'; mail.result='발송 결과를 확인하지 못했습니다. 중복 방지를 위해 자동 재발송하지 않습니다.'
            else:
                mail.state='sent'; mail.sent_at=timezone.now(); mail.result='메일 서버가 발송을 접수했습니다.'
        mail.save(update_fields=['state','result','sent_at'])
        write('mail_'+mail.state, 'AssignmentMail', mail.pk, '업무 배정 메일', after={'task_id':mail.task_id,
            'employee_id':mail.employee_id,'task_title':mail.payload.get('task_title',''),
            'employee_name':mail.payload.get('employee_name',''),'result':mail.result})
