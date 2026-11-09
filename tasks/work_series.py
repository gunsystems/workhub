"""Independent official-work occurrences. Template edits never overwrite old work."""
from datetime import datetime, timedelta
from types import SimpleNamespace
from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from .models import Task, Employee, TaskParticipation, ApprovalStep, ChecklistItem, WorkSeries, WorkOccurrence
from .permissions import visible_tasks, can_edit
from .schedules import ScheduleFields, next_run, SEOUL
from .workflow import record

COPY_FIELDS = ['title','description','completion_criteria','assignee_id','project_id','category_id','management_mode',
               'importance','priority','report_days','review_days','location','customer','work_style','estimated_minutes','assignment_email','assignment_messenger']


class WorkSeriesForm(ScheduleFields):
    starts_at = forms.DateTimeField(label='첫 회차 반복 기준 시각 · 한국 시간', help_text='수행 시간이 없는 업무에서는 회차 생성 시각입니다. 실제 수행 시간은 원본 업무에서 설정하세요.', widget=forms.DateTimeInput(format='%Y-%m-%dT%H:%M',attrs={'type':'datetime-local'}))
    due_offset = forms.IntegerField(label='각 회차 마감까지 일수', min_value=0, max_value=366, initial=0)
    def clean(self):
        data = super().clean()
        if not data.get('recurrence'): self.add_error('recurrence','반복 규칙을 선택하세요.')
        start=data.get('starts_at')
        if not start: return data
        if data.get('repeat_until') and data['repeat_until'] < timezone.localtime(start).date(): self.add_error('repeat_until','종료일은 시작일 이후로 지정하세요.')
        if data.get('schedule_mode')=='completion' and data.get('recurrence') not in ['daily','weekly','monthly_date','yearly']:
            self.add_error('schedule_mode','완료 기준은 매일·매주·매월 날짜·매년에서 사용하세요.')
        if data.get('recurrence')=='weekly' and not data.get('repeat_weekdays'): self.add_error('repeat_weekdays','반복 요일을 선택하세요.')
        return data


def template_snapshot(task, offset):
    return {'fields':{n:getattr(task,n) for n in COPY_FIELDS},'due_offset':offset,
            'start_time':task.start_time.isoformat() if task.start_time else None,
            'duration_seconds':int((task.activity_end-task.activity_start).total_seconds()) if task.activity_start and task.activity_end else None,
            'participants':list(task.participants.values_list('pk',flat=True)),
            'watchers':list(task.watchers.values_list('pk',flat=True)),
            'approvers':list(task.approval_steps.values('approver_id','approver_name','position','stage')),
            'checklist':list(task.checklist.filter(retired=False).values('title','required'))}


def eligible(rule):
    from accounts.lifecycle import account_allowed
    ids=set(rule.template['participants'])|{rule.template['fields']['assignee_id']}
    employees=Employee.objects.filter(pk__in=ids,is_active=True).select_related('user')
    if employees.count()!=len(ids) or not account_allowed(rule.owner): return False
    for employee in employees:
        if employee.user_id and not account_allowed(employee.user): return False
    reviewers=[r['approver_id'] for r in rule.template['approvers']]
    users=get_user_model().objects.filter(pk__in=reviewers,is_active=True)
    if len(users)!=len(reviewers) or any(not account_allowed(u) for u in users): return False
    return not employees.filter(user_id__in=reviewers).exists()


def create_work_occurrence(rule,when):
    existing=rule.occurrences.filter(scheduled_for=when).first()
    if existing: return existing.task,False
    data=rule.template;day=timezone.localtime(when,SEOUL).date()
    fields={**data['fields'],'start_date':day,'due_date':day+timedelta(days=data['due_offset']),
            'creator':rule.owner,'workflow_version':2,'status':'todo',
            'start_time':timezone.localtime(when,SEOUL).time() if data.get('start_time') else None}
    if data['duration_seconds']:
        fields.update(activity_start=when,activity_end=when+timedelta(seconds=data['duration_seconds']))
    project_id=fields.get('project_id')
    if project_id:
        from .models import Project
        project=Project.objects.get(pk=project_id)
        if fields['due_date']>project.due_date or day<project.start_date:
            raise ValidationError('프로젝트 기간 밖 회차입니다. 프로젝트 기간 또는 반복 템플릿을 확인하세요.')
    obj=Task.objects.create(**fields)
    TaskParticipation.objects.bulk_create([TaskParticipation(task=obj,employee_id=i) for i in data['participants']])
    obj.watchers.set(data['watchers'])
    ApprovalStep.objects.bulk_create([ApprovalStep(task=obj,**row) for row in data['approvers']])
    ChecklistItem.objects.bulk_create([ChecklistItem(task=obj,**row) for row in data['checklist']])
    WorkOccurrence.objects.create(series=rule,task=obj,scheduled_for=when)
    record(obj,rule.owner,'created','반복 템플릿에서 생성한 독립 회차',{'series_id':rule.pk,'scheduled_for':when.isoformat()})
    from .collaboration import notify
    notify(obj,'정기 업무의 새 회차가 배정되었습니다.',external=obj.assignment_messenger)
    from .assignment_mail import enqueue as enqueue_assignment_mail
    enqueue_assignment_mail(obj,rule.owner)
    return obj,True


@transaction.atomic
def process_work_rule(pk,now):
    rule=WorkSeries.objects.select_for_update().select_related('owner').get(pk=pk)
    if not rule.enabled or rule.mode!='calendar' or not rule.next_at or rule.next_at>now: return 0
    if not eligible(rule):
        rule.issue='담당자·결재자 또는 작성자의 사용 자격을 확인하고 인계해 주세요.';rule.save(update_fields=['issue']);return 0
    rule.issue=''
    count=0
    while rule.next_at and rule.next_at<=now and count<20:
        if rule.max_count and rule.generated_count>=rule.max_count: rule.next_at=None;break
        when=rule.next_at
        try:
            with transaction.atomic(): _,created=create_work_occurrence(rule,when)
        except ValidationError as exc:
            rule.issue=exc.messages[0];break
        if created: rule.generated_count+=1
        count+=1
        rule.next_at=next_run(rule,when)
    rule.save(update_fields=['next_at','generated_count','issue'])
    return count


def completion_followup(task):
    occurrence=WorkOccurrence.objects.filter(task=task).select_related('series').first()
    if not occurrence: return
    rule=WorkSeries.objects.select_for_update().get(pk=occurrence.series_id)
    if not rule.enabled or rule.mode!='completion' or (rule.max_count and rule.generated_count>=rule.max_count): return
    if rule.occurrences.order_by('-pk').first().pk!=occurrence.pk: return
    if not eligible(rule):
        rule.issue='다음 회차 담당자·결재자의 사용 자격을 확인하세요.';rule.save(update_fields=['issue']);return
    start=timezone.localtime(timezone.now(),SEOUL)
    probe=SimpleNamespace(**{n:getattr(rule,n) for n in ['starts_at','kind','interval','weekdays','month_day','week_number','month_end','until']})
    probe.starts_at=start
    when=next_run(probe,start)
    if not when:return
    try: _,created=create_work_occurrence(rule,when)
    except ValidationError as exc: rule.issue=exc.messages[0];rule.save(update_fields=['issue']);return
    if created: rule.generated_count+=1;rule.save(update_fields=['generated_count'])


@login_required
@transaction.atomic
def series_view(request,pk):
    task=get_object_or_404(visible_tasks(request.user),pk=pk,workflow_version=2)
    if not can_edit(request.user,task): raise PermissionDenied
    occurrence=WorkOccurrence.objects.filter(task=task).select_related('series').first()
    rule=occurrence.series if occurrence else None
    if request.method=='POST' and request.POST.get('action')=='stop' and rule:
        rule.enabled=False;rule.next_at=None;rule.save(update_fields=['enabled','next_at'])
        record(task,request.user,'changed','이후 반복 생성을 중지했습니다. 기존 회차는 보존됩니다.',{'series_id':rule.pk})
        return redirect('work_series',pk=pk)
    form=WorkSeriesForm(request.POST if request.method=='POST' else None,initial={'starts_at':task.activity_start or datetime.combine(task.start_date,task.start_time or datetime.min.time(),tzinfo=SEOUL),'due_offset':(task.due_date-task.start_date).days})
    if request.method=='POST' and not rule and form.is_valid():
        if task.status=='draft': form.add_error(None,'업무를 먼저 발행한 뒤 반복을 설정하세요.')
        else:
            d=form.cleaned_data;start=d['starts_at']
            if timezone.localtime(start).date()!=task.start_date: form.add_error('starts_at','첫 회차는 현재 업무의 시작일로 지정하세요. 이후 회차는 자동 계산됩니다.')
            else:
                rule=WorkSeries.objects.create(template_task=task,owner=request.user,template=template_snapshot(task,d['due_offset']),
                    starts_at=start,kind=d['recurrence'],mode=d.get('schedule_mode') or 'calendar',interval=d.get('repeat_interval') or 1,
                    weekdays=d.get('repeat_weekdays') or [timezone.localtime(start).weekday()],month_day=d.get('repeat_month_day') or timezone.localtime(start).day,
                    week_number=d.get('repeat_week_number') or 1,month_end=d.get('missing_day') or 'last',until=d.get('repeat_until'),max_count=d.get('repeat_count'))
                rule.next_at=next_run(rule,start) if rule.mode=='calendar' and rule.max_count!=1 else None;rule.save(update_fields=['next_at'])
                WorkOccurrence.objects.create(series=rule,task=task,scheduled_for=start)
                record(task,request.user,'changed','반복 템플릿 등록 · 현재 업무를 첫 회차로 포함',{'series_id':rule.pk})
                return redirect('work_series',pk=pk)
    preview=[]
    if rule and rule.enabled and rule.mode=='calendar':
        current=rule.next_at
        remaining=rule.max_count-rule.generated_count if rule.max_count else 12
        while current and len(preview)<min(12,remaining): preview.append(current);current=next_run(rule,current)
    return render(request,'tasks/work_series.html',{'task':task,'rule':rule,'form':form,'preview':preview,'occurrences':rule.occurrences.select_related('task').order_by('-scheduled_for')[:50] if rule else []})
