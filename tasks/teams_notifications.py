"""Transactional Teams outbox. No network access occurs while saving business data."""
from datetime import timedelta
from django.conf import settings
from django.db import transaction
from django.db.models import F
from django.urls import reverse
from django.utils import timezone
from .models import TeamsConnection, TeamsDelivery
from . import teams_transport as transport


def eligible(connection):
    from accounts.lifecycle import account_allowed
    user = connection.user
    access = getattr(user, 'workhub_access', None)
    cfg = transport.config()
    if cfg and connection.subject:
        from .models import DocumentConnection, MicrosoftConnection
        known = DocumentConnection.objects.filter(user=user, tenant=cfg[0]).first()
        meeting = MicrosoftConnection.objects.filter(user=user).first() if settings.MICROSOFT_TENANT_ID == cfg[0] else None
        if (known and str(known.subject) != connection.subject) or (meeting and meeting.subject.lower() != connection.subject):
            return False
    return bool(transport.enabled() and connection.enabled and connection.connected_at and
        connection.conversation and cfg and (connection.tenant, connection.app) == cfg[:2] and
        user.email and access and access.verified_at and
        user.email.casefold() == access.email.casefold() == connection.email.casefold() and account_allowed(user))


def enqueue(user_id, source, source_id, kind, snapshot=None, key='', dedupe_key=''):
    if not transport.enabled() or kind not in ('assignment', 'pending', 'result') or not user_id:
        return
    connection = TeamsConnection.objects.select_related('user').filter(user_id=user_id).first()
    if not connection or not eligible(connection) or not getattr(connection, kind):
        return
    return TeamsDelivery.objects.get_or_create(dedupe_key=dedupe_key or f'{source}:{source_id}:{key}:{kind}:{user_id}', defaults={
        'user_id': user_id, 'connection_generation': connection.generation,
        'source': source, 'source_id': source_id, 'kind': kind, 'snapshot': snapshot or {}})[0]


def assignment(task, employee_ids=None):
    if not transport.enabled() or task.status in ('draft', 'cancelled', 'done'):
        return
    from .models import Employee
    ids = set(employee_ids) if employee_ids is not None else set(task.participants.values_list('pk', flat=True)) | {task.assignee_id}
    stamp = task.assigned_at.isoformat() if task.assigned_at else ''
    for employee in Employee.objects.filter(pk__in=ids, is_active=True, user__is_active=True):
        enqueue(employee.user_id, 'assignment', task.pk, 'assignment',
                {'employee': employee.pk, 'assigned_at': stamp}, key=f'{task.revision}:{stamp}')


def notice(row):
    sources = {'LeaveNotice': 'leave', 'ProposalNotice': 'proposal', 'CertificateNotice': 'certificate'}
    enqueue(row.user_id, sources[type(row).__name__], row.pk, row.kind)


def task_event(event):
    if not transport.enabled():
        return
    if event.kind == 'handover' and event.snapshot.get('transfer_id'):
        transfer_event(event)
        return
    task = event.task
    if event.kind not in ('submitted', 'approved', 'returned', 'override', 'handover'):
        return
    if task.status == 'review':
        step = task.approval_steps.exclude(decision='approved').first()
        if step:
            for user_id in task.approval_steps.filter(stage=step.stage).exclude(decision='approved').values_list('approver_id', flat=True):
                enqueue(user_id, 'task_event', event.pk, 'pending', {'round': task.report_round},
                        dedupe_key=f'task-turn:{task.pk}:{task.report_round}:{step.stage}:{user_id}')
    if task.status == 'done' or event.kind == 'returned' or (event.kind == 'override' and event.snapshot.get('decision') == 'return'):
        assignee_user = task.assignee.user_id if task.assignee_id else None
        users = set(task.participants.values_list('user_id', flat=True)) | {assignee_user, task.creator_id}
        for user_id in users:
            enqueue(user_id, 'task_event', event.pk, 'result', {'status': task.status, 'round': task.report_round})


def quick_event(event):
    if not transport.enabled():
        return
    item = event.request
    if event.action == '전달':
        enqueue(item.assignee.user_id, 'quick', event.pk, 'assignment', {'revision': item.revision})
    elif event.action in ('결과 회신', '확인 완료', '취소', '추가 확인 요청'):
        user_id = item.requester_id if event.action == '결과 회신' else item.assignee.user_id
        enqueue(user_id, 'quick', event.pk, 'result', {'revision': item.revision})


def transfer_event(event):
    from .models import TaskTransfer
    from .transfers import current_step
    item = TaskTransfer.objects.select_related('source', 'target').filter(pk=event.snapshot['transfer_id'], task_id=event.task_id).first()
    if not item:
        return
    data = {'transfer': item.pk, 'revision': item.revision, 'state': item.state}
    step = current_step(item) if item.state == 'pending' else None
    if step:
        enqueue(step.approver.user_id, 'transfer', event.pk, 'pending', data,
                dedupe_key=f'transfer-turn:{item.pk}:{step.pk}:{step.approver.user_id}')
    elif item.state in ('acceptance', 'done', 'rejected', 'withdrawn'):
        for user_id in {item.requester_id, item.source.user_id, item.target.user_id}:
            enqueue(user_id, 'transfer', event.pk, 'result', data)


def destination(job):
    """Recheck live authorization and current approval turn; never trust a stored URL."""
    user = job.user
    if job.source == 'transfer':
        from .models import TaskEvent
        from .transfers import visible, current_step, can_decide, ACTIVE
        event = TaskEvent.objects.filter(pk=job.source_id).first()
        item = visible(user).filter(pk=job.snapshot.get('transfer'), revision=job.snapshot.get('revision'),
                                    state=job.snapshot.get('state')).select_related('source', 'target').first()
        if not event or not item or event.task_id != item.task_id or event.snapshot.get('transfer_id') != item.pk:
            return None
        if job.kind == 'pending':
            step = current_step(item) if item.state == 'pending' else None
            if not step or step.approver.user_id != user.pk or not can_decide(user, item) or item.task.status not in ACTIVE:
                return None
        elif user.pk not in {item.requester_id, item.source.user_id, item.target.user_id}:
            return None
        return ('업무 이관 결재가 필요합니다.' if job.kind == 'pending' else '업무 이관 처리 결과를 확인해 주세요.'), reverse('transfer_detail', args=[item.pk])
    if job.source == 'assignment':
        from .models import Task
        from .permissions import visible_tasks
        task = visible_tasks(user).filter(pk=job.source_id).first()
        if not task or task.status not in ('todo', 'doing', 'blocked'):
            return None
        member = task.participants.filter(pk=job.snapshot.get('employee'), user=user, is_active=True).exists()
        owner = task.assignee_id == job.snapshot.get('employee') and task.assignee.user_id == user.pk and task.assignee.is_active
        stamp = task.assigned_at.isoformat() if task.assigned_at else ''
        if not (member or owner) or stamp != job.snapshot.get('assigned_at'):
            return None
        return '새 업무가 배정되었습니다.', reverse('task_detail', args=[task.pk])
    if job.source in ('leave', 'proposal', 'certificate'):
        from .models import LeaveNotice, ProposalNotice, CertificateNotice
        model = {'leave': LeaveNotice, 'proposal': ProposalNotice, 'certificate': CertificateNotice}[job.source]
        row = model.objects.filter(pk=job.source_id, user=user, kind=job.kind).first()
        if not row:
            return None
        if job.source == 'leave':
            from .leave import visible
            from .leave_notifications import actionable
            item = row.event.request
            valid = visible(user).filter(pk=item.pk).exists() and (actionable(row) if job.kind == 'pending' else item.revision == row.snapshot.get('revision'))
            route = 'leave_detail'
        elif job.source == 'proposal':
            from .proposals import visible, current
            item = row.event.proposal
            step = current(item)
            valid = visible(user).filter(pk=item.pk).exists() and str(item.revision) == row.event.snapshot.get('revision')
            if job.kind == 'pending':
                valid = valid and step and step.approver.user_id == user.pk
            route = 'proposal_detail'
        else:
            from .certificates import visible, pending_for
            item = row.event.request
            valid = visible(user).filter(pk=item.pk).exists() and item.revision == row.event.revision
            if job.kind == 'pending':
                valid = valid and pending_for(user).filter(pk=item.pk).exists()
            route = 'certificate_detail'
        if not valid:
            return None
        # No applicant names, dates, money, HR details or attachments in Teams.
        title = '확인할 결재·신청이 있습니다.' if job.kind == 'pending' else '신청한 결재·서류의 처리 결과가 도착했습니다.'
        return title, reverse(route, args=[item.pk])
    if job.source == 'task_event':
        from .models import TaskEvent
        from .permissions import visible_tasks, can_review
        event = TaskEvent.objects.filter(pk=job.source_id).select_related('task').first()
        if not event or not visible_tasks(user).filter(pk=event.task_id).exists():
            return None
        task = event.task
        if task.report_round != job.snapshot.get('round'):
            return None
        if job.kind == 'pending' and not can_review(user, task):
            return None
        if job.kind == 'result' and task.status != job.snapshot.get('status'):
            return None
        if job.kind == 'result' and not (task.creator_id == user.pk or task.assignee.user_id == user.pk or task.participants.filter(user=user, is_active=True).exists()):
            return None
        return ('업무 결재가 필요합니다.' if job.kind == 'pending' else '업무 처리 결과를 확인해 주세요.'), reverse('task_detail', args=[task.pk])
    if job.source == 'quick':
        from .models import QuickEvent
        from .quick import visible
        event = QuickEvent.objects.filter(pk=job.source_id).select_related('request__assignee').first()
        if not event:
            return None
        item = event.request
        expected = item.assignee.user_id if job.kind == 'assignment' or event.action != '결과 회신' else item.requester_id
        if user.pk != expected or item.revision != job.snapshot.get('revision') or not visible(user).filter(pk=item.pk).exists():
            return None
        return ('새 간단 업무가 도착했습니다.' if job.kind == 'assignment' else '간단 업무 처리 결과를 확인해 주세요.'), reverse('quick_detail', args=[item.pk])
    return None


def deliver_pending(limit=5):
    if not transport.enabled():
        return
    now = timezone.now()
    TeamsDelivery.objects.filter(state='sending', claimed_until__lt=now).update(state='unknown', outcome='interrupted')
    ids = list(TeamsDelivery.objects.filter(state='pending', due_at__lte=now).order_by('pk').values_list('pk', flat=True)[:limit])
    for pk in ids:
        if not TeamsDelivery.objects.filter(pk=pk, state='pending').update(state='sending', attempts=F('attempts') + 1, claimed_until=now + timedelta(minutes=3)):
            continue
        job = TeamsDelivery.objects.select_related('user').get(pk=pk)
        state, outcome, message_id = 'skipped', 'not_current', ''
        due = now
        try:
            connection = TeamsConnection.objects.select_related('user').filter(user_id=job.user_id).first()
            allowed = connection and eligible(connection) and connection.generation == job.connection_generation and getattr(connection, job.kind, False)
            target = destination(job) if allowed and job.created_at > now - timedelta(days=1) else None
            if target:
                base = settings.WORKHUB_BASE_URL.rstrip('/')
                if not base.startswith('https://'):
                    raise ValueError('base URL')
                response = transport.send(connection, target[0], base + target[1])
                if response.status_code in (200, 201, 202):
                    state, outcome = 'sent', 'accepted'
                    try:
                        message_id = str(response.json().get('id', ''))[:500]
                    except (ValueError, AttributeError):
                        pass
                elif response.status_code == 429 and job.attempts < 4:
                    state, outcome = 'pending', 'throttled'
                    try:
                        seconds = int(response.headers.get('Retry-After', '60'))
                    except ValueError:
                        seconds = 60
                    due = now + timedelta(seconds=max(30, min(seconds, 3600)))
                else:
                    # 5xx/timeouts can be ambiguous: do not blindly send a second copy.
                    state, outcome = ('failed', 'http_' + str(response.status_code)) if response.status_code < 500 else ('unknown', 'provider_uncertain')
                    if response.status_code in (403, 404, 410):
                        TeamsConnection.objects.filter(pk=connection.pk, generation=connection.generation).update(enabled=False)
        except Exception:
            # Never log provider bodies, credentials, link codes, or private business data.
            state, outcome = 'unknown', 'delivery_uncertain'
        TeamsDelivery.objects.filter(pk=pk, state='sending').update(state=state, outcome=outcome,
            due_at=due, message_id=message_id, sent_at=timezone.now() if state == 'sent' else None)
        from .audit import write
        write('teams_' + state, 'TeamsDelivery', pk, 'Teams 알림', after={'종류': job.kind, '발송 상태': state, '결과': outcome})
