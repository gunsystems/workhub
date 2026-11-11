from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone
from .models import Task, TaskEvent
from .permissions import visible_tasks, can_edit, can_review, can_report


def snapshot(task):
    return {name: str(getattr(task,name)) for name in ['title','description','delay_reason','assignee_id','status','progress','importance','priority','start_date','start_time','due_date','original_due_date','quality_score','project_id','completion_criteria','report_days','review_days','accepted_at','category_id','management_mode','activity_start','activity_end','customer','location','work_style','estimated_minutes','next_action','next_check_date']}


def record(task, user, kind, body, extra=None):
    from .audit import write
    from .schedule_control import observe_schedule
    extra={**(extra or {}),**observe_schedule(task,kind,extra or {})}
    if task.status=='draft':
        write('work_event','Task',task.pk,'비공개 초안',after={'kind':kind,'private':True},actor=user)
    else:
        write('work_event','Task',task.pk,task.title,
              after={'kind':kind,'body':body,'details':{**snapshot(task),**(extra or {}), 'participants':list(task.assignments.values('employee_id','progress')), 'approval_line':list(task.approval_steps.values('approver_id','stage','decision'))}},actor=user)
    result=TaskEvent.objects.create(task=task,author=user,author_name=user.get_username(),kind=kind,body=body,snapshot={**snapshot(task),**(extra or {})})
    if task.status!='done':
        from .models import WorkRequest
        intake=WorkRequest.objects.filter(task=task,confirmed_at__isnull=False).first()
        if intake:
            from .requests import event
            previous={'confirmed_at':str(intake.confirmed_at),'confirmation_note':intake.confirmation_note}
            intake.confirmed_at=None;intake.confirmation_note='';intake.revision+=1;intake.save(update_fields=['confirmed_at','confirmation_note','revision'])
            event(intake,user,'결과 확인 재요청','수행 업무가 다시 진행되어 기존 결과 확인을 해제했습니다.',previous)
    return result


@transaction.atomic
def transition(user, pk, action, data):
    # Keep the status change and immutable event in one database transaction.
    task = visible_tasks(user).select_for_update().get(pk=pk)
    if task.workflow_version == 2:
        raise ValidationError('새 업무 화면의 처리 버튼을 이용하세요.')
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
            task.progress=max(task.progress,1)
            if task.status=='todo':task.status='doing'
            task.save(update_fields=['accepted_at','progress','status'])
            record(task,user,'accepted','배정된 업무를 확인했습니다.')
        return task
    if action == 'feedback':
        saved_event=record(task,user,'feedback',data['body'])
        from .collaboration import notify
        notify(task,'새 의견: '+data['body'][:150],external=task.assignment_messenger,comment_event=saved_event)
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
            raise ValidationError('업무 내용과 기한을 확인하고 먼저 배정 확인을 완료하세요.')
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
    from .progress_reporting import next_report_number
    report_details={'progress_report_number':next_report_number(task,user),'performer_progress':task.progress,'action':'progress'} if action=='progress' else {}
    record(task,user,kind,data['body'],{'before':before,'report_gap_days':report_gap,'report_interval_days':task.report_days,**report_details})
    return task
