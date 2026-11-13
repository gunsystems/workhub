"""Approved performer replacement. No reassignment happens at request time."""
from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction, OperationalError, IntegrityError
from functools import wraps
from django.db.models import Q, F
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from .models import Employee, Task, TaskParticipation, TaskTransfer, TransferStep, WorkNotification
from .permissions import profile, is_company_admin, visible_tasks, can_view_company

ACTIVE = ['todo', 'doing', 'blocked']


def retry_busy(fn):
    @wraps(fn)
    def run(*args, **kwargs):
        for attempt in range(3):
            try:
                return fn(*args, **kwargs)
            except IntegrityError as exc:
                raise ValidationError('이미 진행 중인 이관 요청이 있거나 배정 정보가 변경되었습니다. 최신 상태를 확인해 주세요.') from exc
            except OperationalError as exc:
                if 'locked' not in str(exc).lower():
                    raise
                if attempt == 2:
                    raise ValidationError('다른 처리가 진행 중입니다. 잠시 후 다시 확인해 주세요.') from exc
                import time
                time.sleep(.05)
    return run


def eligible_employees():
    return Employee.objects.filter(is_active=True).filter(Q(user__isnull=True) | Q(user__is_active=True))


def can_request(user, task, source=None):
    if not user.is_authenticated or not user.is_active or task.workflow_version != 2 or task.status not in ACTIVE:
        return False
    if hasattr(task, 'meeting'):
        return False
    emp = profile(user)
    if is_company_admin(user) or task.creator_id == user.pk or (emp and emp.position == 'executive'):
        return True
    if not emp:
        return False
    if source:
        return source.user_id == user.pk or (emp.department_id == source.department_id and
            (emp.position in ['leader', 'head'] or emp.role == 'manager'))
    return task.assignments.filter(employee=emp).exists() or (
        (emp.position in ['leader', 'head'] or emp.role == 'manager') and
        task.assignments.filter(employee__department_id=emp.department_id).exists())


def approval_line(task, source, target, requester):
    # Never substitute a technical administrator for a business approver.
    excluded = {source.pk, target.pk}
    actor = profile(requester)
    if actor:
        excluded.add(actor.pk)
    available = eligible_employees().exclude(pk__in=excluded).order_by('pk')
    def representative():
        rows = list(available.filter(position='executive'))
        if len(rows) != 1:
            raise ValidationError('이관을 승인할 대표를 직원 정보에서 지정해 주세요.')
        return rows[0]
    def team_approver(employee):
        rows = available.filter(department_id=employee.department_id).filter(
            Q(position='leader') | Q(role='manager'))
        if employee.supervisor_id:
            supervisor = rows.filter(pk=employee.supervisor_id).first()
            if supervisor:
                return supervisor
        leaders = list(rows)
        if len(leaders) == 1:
            return leaders[0]
        if len(leaders) > 1:
            raise ValidationError('해당 직원의 직속 상위자를 지정해 주세요.')
        return representative()
    needs_rep = source.position in ['leader', 'head', 'executive'] or source.role == 'manager' or (
        task.approval_steps.filter(approver__employee__position='executive').exists())
    first = representative() if needs_rep else team_approver(source)
    result = [(first, '대표 승인' if first.position == 'executive' else '소속 팀장 승인')]
    if source.department_id != target.department_id:
        receiver = team_approver(target)
        if receiver.pk != first.pk:
            result.append((receiver, '인수 부서 승인'))
    return result


def validate_pair(task, source, target):
    if task.status not in ACTIVE or task.is_deleted:
        raise ValidationError('진행 중인 업무만 이관할 수 있습니다.')
    if not task.assignments.filter(employee=source).exists():
        raise ValidationError('기존 담당자가 변경되었습니다. 업무 화면에서 다시 확인해 주세요.')
    if task.transfers.filter(target=source, state='acceptance').exists():
        raise ValidationError('이관받은 업무의 배정 확인을 마친 후 다시 이관할 수 있습니다.')
    if not eligible_employees().filter(pk=target.pk).exists():
        raise ValidationError('사용 가능한 인수자를 선택해 주세요.')
    if source.pk == target.pk or task.assignments.filter(employee=target).exists():
        raise ValidationError('현재 수행자를 제외한 다른 직원을 선택해 주세요.')
    if task.approval_steps.filter(approver_id=target.user_id).exists() and target.user_id:
        raise ValidationError('업무 결재자는 수행자로 이관받을 수 없습니다.')


def ping(item, employees=(), users=(), text=''):
    from .hiworks import enqueue
    ids = set(users) | {e.user_id for e in employees if e.user_id}
    for uid in ids:
        if not uid:
            continue
        notification = WorkNotification.objects.create(user_id=uid, task=item.task,
            message=f'업무 이관 · {text}'[:240])
        if item.task.assignment_messenger:
            enqueue(notification)
        # New assignees receive the existing assignment mail after final approval.
        if item.task.assignment_email and not (item.state == 'acceptance' and uid == item.target.user_id):
            employee = Employee.objects.filter(user_id=uid, is_active=True).select_related('user').first()
            access = getattr(employee.user, 'workhub_access', None) if employee else None
            if access and access.verified_at:
                from .models import AssignmentMail
                AssignmentMail.objects.get_or_create(dedupe_key=f'transfer:{item.pk}:{item.revision}:{uid}',
                    defaults={'task': item.task, 'employee': employee, 'payload': {'kind': 'transfer',
                        'transfer_id': item.pk, 'revision': item.revision, 'user_id': uid,
                        'email': access.email, 'text': text}})


def event(item, user, text, extra=None):
    from .workflow import record
    return record(item.task, user, 'handover', text, {
        'transfer_id': item.pk, 'source': item.source_id, 'target': item.target_id,
        'state': item.state, **(extra or {})})


@retry_busy
@transaction.atomic
def request_transfer(user, task_pk, source, target, reason, notes='', revision=None):
    task = get_object_or_404(visible_tasks(user).select_for_update(), pk=task_pk)
    if not can_request(user, task, source):
        raise PermissionDenied
    if revision != task.revision:
        raise ValidationError('업무 내용이 변경되었습니다. 화면을 다시 열어 주세요.')
    if not reason.strip():
        raise ValidationError('이관 사유를 입력해 주세요.')
    validate_pair(task, source, target)
    if task.transfers.filter(source=source, state__in=['pending', 'acceptance']).exists():
        raise ValidationError('이미 진행 중인 이관 요청이 있습니다.')
    line = approval_line(task, source, target, user)
    if not Task.objects.filter(pk=task.pk, revision=revision).update(revision=F('revision') + 1):
        raise ValidationError('다른 변경이 진행 중입니다. 다시 확인해 주세요.')
    task.refresh_from_db()
    assignment = task.assignments.get(employee=source)
    snapshot = {key: str(getattr(assignment, key)) for key in ['assigned_at', 'accepted_at', 'completed_at', 'progress']}
    snapshot['primary_id'] = task.assignee_id
    item = TaskTransfer.objects.create(task=task, source=source, target=target, requester=user,
        reason=reason.strip(), notes=notes.strip(), primary=task.assignee_id == source.pk, source_assignment=snapshot)
    for n, (approver, label) in enumerate(line, 1):
        TransferStep.objects.create(transfer=item, position=n, approver=approver, label=label)
    event(item, user, f'이관 요청: {source.name} → {target.name}\n{reason}', {'assignment': snapshot})
    ping(item, [source, line[0][0]], [user.pk], '이관 승인 요청이 등록되었습니다.')
    return item


def current_step(item):
    return item.steps.exclude(decision='approved').first()


def can_decide(user, item):
    step = current_step(item)
    emp = profile(user)
    return bool(item.state == 'pending' and emp and step and step.approver_id == emp.pk and
        user.pk != item.requester_id and emp.pk not in [item.source_id, item.target_id])


@retry_busy
@transaction.atomic
def decide(user, pk, revision, action, note=''):
    item = TaskTransfer.objects.select_for_update().select_related('task', 'source', 'target', 'requester').get(pk=pk)
    task = Task.all_objects.select_for_update().get(pk=item.task_id)
    item.task = task
    if item.revision != revision or item.state != 'pending':
        raise ValidationError('이미 처리되었거나 변경된 요청입니다. 최신 상태를 확인해 주세요.')
    if action == 'withdraw':
        if item.requester_id != user.pk and not is_company_admin(user):
            raise PermissionDenied
    elif not can_decide(user, item):
        raise PermissionDenied
    if action not in ['approve', 'reject', 'withdraw']:
        raise ValidationError('처리 방법을 선택해 주세요.')
    if action == 'reject' and not note.strip():
        raise ValidationError('반려 사유를 입력해 주세요.')
    if not TaskTransfer.objects.filter(pk=pk, revision=revision, state='pending').update(revision=F('revision') + 1):
        raise ValidationError('이미 처리된 요청입니다.')
    step = current_step(item)
    now = timezone.now()
    if action == 'withdraw':
        item.state = 'withdrawn'
    else:
        if action == 'approve':
            validate_pair(task, item.source, item.target)
            # Changes in positions/departments/approval requirements invalidate the old route.
            expected = [(emp.pk, label) for emp, label in approval_line(task, item.source, item.target, item.requester)]
            actual = list(item.steps.values_list('approver_id', 'label'))
            if expected != actual:
                raise ValidationError('소속 또는 승인 기준이 변경되었습니다. 요청을 취소하고 다시 작성해 주세요.')
            assignment = task.assignments.get(employee=item.source)
            if str(assignment.assigned_at) != item.source_assignment['assigned_at'] or task.assignee_id != item.source_assignment['primary_id']:
                raise ValidationError('배정 정보가 변경되었습니다. 새 이관 요청을 작성해 주세요.')
        step.decision = 'approved' if action == 'approve' else 'rejected'
        step.note = note.strip(); step.decided_at = now; step.save()
        if action == 'reject':
            item.state = 'rejected'
        elif not item.steps.exclude(decision='approved').exists():
            # Snapshot the latest work, including reports made while approval was pending.
            item.source_assignment.update({key: str(getattr(assignment, key)) for key in ['accepted_at', 'completed_at', 'progress']})
            task.assignments.filter(employee=item.source).delete()
            TaskParticipation.objects.create(task=task, employee=item.target, assigned_at=now)
            if item.primary:
                task.assignee = item.target; task.accepted_at = None
                task.last_report_at = None; task.assigned_at = now
            from .collaboration import refresh_participation_progress
            refresh_participation_progress(task)
            item.state = 'acceptance'; item.approved_at = now
            from .assignment_mail import enqueue
            task.revision += 1; task.save()
            enqueue(task, user, {item.target_id})
    item.revision += 1
    if item.state in ['withdrawn', 'rejected']:
        item.closed_at = now
    item.save()
    event(item, user, f'이관 {dict(approve="승인", reject="반려", withdraw="요청 취소")[action]}: {item.source.name} → {item.target.name}' + (f'\n{note}' if note else ''),
          {'assignment': item.source_assignment})
    next_step = current_step(item) if item.state == 'pending' else None
    recipients = [item.source] + ([item.target] if item.state == 'acceptance' else []) + ([next_step.approver] if next_step else [])
    ping(item, recipients, [item.requester_id], item.get_state_display())
    return item


def accepted(task, employee, user):
    """Called inside the existing assignment acceptance transaction."""
    for item in task.transfers.select_for_update().filter(target=employee, state='acceptance'):
        item.state = 'done'; item.accepted_at = timezone.now(); item.closed_at = item.accepted_at
        item.revision += 1; item.save()
        event(item, user, f'인수 확인 완료: {item.target.name}')
        ping(item, [item.source], [item.requester_id], '새 담당자가 인수를 확인했습니다.')


def close_for_task(task, user):
    """End outstanding handovers when their parent work is cancelled or removed."""
    for item in task.transfers.select_for_update().filter(state__in=['pending', 'acceptance']):
        item.state = 'withdrawn'; item.closed_at = timezone.now(); item.revision += 1; item.save()
        event(item, user, '업무 취소·삭제로 이관 요청이 종료되었습니다.')
        ping(item, [item.source, item.target], [item.requester_id], '업무 취소·삭제로 이관 요청이 종료되었습니다.')


def visible(user):
    qs = TaskTransfer.objects.select_related('task', 'source__department', 'target__department')
    if can_view_company(user):
        return qs
    return qs.filter(Q(requester=user) | Q(source__user=user) | Q(target__user=user) |
                     Q(steps__approver__user=user)).distinct()


def deliver_mail(job):
    from accounts.lifecycle import account_allowed
    from accounts.services import deliver
    from django.conf import settings
    from django.urls import reverse
    from django.template.loader import render_to_string
    employee = job.employee
    user = employee.user if employee and employee.user_id else None
    access = getattr(user, 'workhub_access', None) if user else None
    item = visible(user).filter(pk=job.payload.get('transfer_id')).first() if user and user.is_active else None
    if not (item and employee.is_active and access and access.verified_at and account_allowed(user)
        and user.pk == job.payload.get('user_id') and user.email.lower() == access.email.lower() == str(job.payload.get('email','')).lower()
        and item.task.assignment_email and not item.task.is_deleted and item.revision == job.payload.get('revision')):
        job.state = 'skipped'; job.result = '현재 이관 상태 또는 접근 권한·알림 설정 변경으로 제외했습니다.'
    else:
        url = settings.WORKHUB_BASE_URL.rstrip('/') + reverse('transfer_detail', args=[item.pk])
        text = job.payload.get('text', '업무 이관 상태를 확인해 주세요.')
        try:
            deliver('[Workhub] 업무 이관 안내', f'{item.task.title}\n{text}\n{url}', access.email,
                html_body=render_to_string('tasks/email_transfer.html', {'item': item, 'text': text, 'url': url}))
        except Exception:
            job.state = 'unknown'; job.result = '발송 결과 확인 필요 · 자동 재발송하지 않습니다.'
        else:
            job.state = 'sent'; job.sent_at = timezone.now(); job.result = '메일 서버가 발송을 접수했습니다.'
    job.save(update_fields=['state','result','sent_at'])
    from .audit import write
    write('mail_'+job.state, 'AssignmentMail', job.pk, '업무 이관 메일', after={'transfer_id': job.payload.get('transfer_id'), 'result': job.result})


class EmployeeChoice(forms.ModelChoiceField):
    def label_from_instance(self, obj):
        return f'{obj.department} · {obj.name} · {obj.get_position_display()}'


class TransferForm(forms.Form):
    revision = forms.IntegerField(widget=forms.HiddenInput)
    source = EmployeeChoice(label='현재 담당자', queryset=Employee.objects.none())
    target = EmployeeChoice(label='인수할 직원', queryset=Employee.objects.none())
    reason = forms.CharField(label='이관 사유', max_length=2000, widget=forms.Textarea(attrs={'rows': 3}))
    notes = forms.CharField(label='인계 내용 · 남은 업무 · 참고자료 안내', required=False, max_length=10000,
        widget=forms.Textarea(attrs={'rows': 5}), help_text='업무에 첨부된 자료는 인수자에게 그대로 연결됩니다. 추가 자료는 업무 상세의 업무자료에서 등록하세요.')

    def __init__(self, *args, task, user, **kwargs):
        super().__init__(*args, **kwargs)
        ids = [e.pk for e in task.participants.all() if can_request(user, task, e)]
        self.fields['source'].queryset = Employee.objects.filter(pk__in=ids).select_related('department')
        self.fields['target'].queryset = eligible_employees().exclude(pk__in=task.participants.values('pk')).select_related('department')
        self.initial.update(revision=task.revision, source=task.assignee_id if task.assignee_id in ids else next(iter(ids), None))


@login_required
def create(request, task_pk):
    task = get_object_or_404(visible_tasks(request.user), pk=task_pk)
    if not can_request(request.user, task):
        raise PermissionDenied
    form = TransferForm(request.POST or None, task=task, user=request.user)
    if request.method == 'POST' and form.is_valid():
        data = form.cleaned_data
        try:
            item = request_transfer(request.user, task.pk, data['source'], data['target'], data['reason'], data['notes'], data['revision'])
        except ValidationError as exc:
            form.add_error(None, exc)
        else:
            return redirect('transfer_detail', pk=item.pk)
    return render(request, 'tasks/transfer_form.html', {'task': task, 'form': form})


class DecisionForm(forms.Form):
    revision = forms.IntegerField(widget=forms.HiddenInput)
    action = forms.ChoiceField(choices=[('approve', '승인'), ('reject', '반려'), ('withdraw', '요청 취소')])
    note = forms.CharField(label='처리 의견', required=False, max_length=2000, widget=forms.Textarea(attrs={'rows': 3}))


@login_required
def detail(request, pk):
    item = get_object_or_404(visible(request.user), pk=pk)
    form = DecisionForm(request.POST or None, initial={'revision': item.revision})
    allowed = [('approve', '승인'), ('reject', '반려')] if can_decide(request.user, item) else []
    if item.state == 'pending' and (item.requester_id == request.user.pk or is_company_admin(request.user)):
        allowed.append(('withdraw', '요청 취소'))
    form.fields['action'].choices = allowed
    if request.method == 'POST' and form.is_valid():
        try:
            decide(request.user, item.pk, form.cleaned_data['revision'], form.cleaned_data['action'], form.cleaned_data['note'])
        except ValidationError as exc:
            form.add_error(None, exc)
        else:
            messages.success(request, '이관 요청을 처리했습니다.')
            return redirect('transfer_detail', pk=item.pk)
    return render(request, 'tasks/transfer_detail.html', {'item': item, 'steps': item.steps.select_related('approver__department'),
        'form': form if allowed else None, 'can_open_task': visible_tasks(request.user).filter(pk=item.task_id).exists(),
        'can_accept': item.state == 'acceptance' and item.target.user_id == request.user.pk and item.task.status in ACTIVE})
