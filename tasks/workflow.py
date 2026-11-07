from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone
from .models import Task, TaskEvent
from .permissions import visible_tasks, can_edit, can_review, can_report


def snapshot(task):
    return {name: str(getattr(task,name)) for name in ['title','description','delay_reason','assignee_id','status','progress','importance','priority','start_date','due_date','original_due_date','quality_score','project_id','completion_criteria','report_days','review_days','accepted_at']}


def record(task, user, kind, body, extra=None):
    return TaskEvent.objects.create(task=task,author=user,author_name=user.get_username(),kind=kind,body=body,snapshot={**snapshot(task),**(extra or {})})


@transaction.atomic
def transition(user, pk, action, data):
    # Keep the status change and immutable event in one database transaction.
    task = visible_tasks(user).select_for_update().get(pk=pk)
    if task.workflow_version == 2:
        raise ValidationError('새 공식 업무 화면의 처리 버튼을 이용하세요.')
    if action in ['review','feedback']:
        if not can_review(user,task):
            raise PermissionDenied
    elif not can_report(user,task):
        raise PermissionDenied
    before = snapshot(task)
    last_report = timezone.localtime(task.last_report_at).date() if task.last_report_at else task.start_date
    if task.assigned_at: last_report = max(last_report, timezone.localtime(task.assigned_at).date())
    report_gap = max(0,(timezone.localdate()-last_report).days) if action in ['progress','submit'] else 0
    if action == 'accept':
        if task.status in ['review','done']:
            raise ValidationError('완료 또는 검토 대기 업무입니다.')
        if task.accepted_at is None:
            task.accepted_at = timezone.now()
            task.save(update_fields=['accepted_at'])
            record(task,user,'accepted','배정된 업무를 확인했습니다.')
        return task
    if action == 'feedback':
        record(task,user,'feedback',data['body'])
        return task
    if action == 'review':
        if task.status != 'review':
            raise ValidationError('검토 대기 업무만 승인하거나 보완 요청할 수 있습니다.')
        if data['decision'] == 'approve':
            if task.checklist.filter(required=True,checked=False,retired=False).exists():
                raise ValidationError('미완료 필수 체크리스트가 있어 승인할 수 없습니다.')
            task.status = 'done'
            task.progress = 100
            task.completed_at = task.submitted_at
            task.approved_at = timezone.now()
            task.quality_score = data['quality_score']
            kind = 'approved'
        else:
            task.status = 'doing'
            task.submitted_at = None
            task.progress = min(task.progress,99)
            kind = 'returned'
    else:
        if task.status in ['review','done']:
            raise ValidationError('검토 대기 또는 완료된 업무에는 진행·완료 보고를 추가할 수 없습니다.')
        if action == 'submit' and task.checklist.filter(required=True,checked=False,retired=False).exists():
            raise ValidationError('필수 체크리스트를 모두 완료한 뒤 완료 보고하세요.')
        if task.accepted_at is None:
            task.accepted_at = timezone.now()
            record(task,user,'accepted','담당자 보고로 배정을 확인했습니다.')
        if action == 'progress':
            task.progress = data['progress']
            task.delay_reason = data['delay_reason']
            task.status = 'blocked' if task.delay_reason else 'doing'
            kind = 'progress'
        elif action == 'submit':
            task.status = 'review'
            task.submitted_at = timezone.now()
            task.progress = 100
            kind = 'submitted'
        else:
            raise ValidationError('알 수 없는 작업입니다.')
        task.last_report_at = timezone.now()
    task.save()
    record(task,user,kind,data['body'],{'before':before,'report_gap_days':report_gap,'report_interval_days':task.report_days})
    return task
