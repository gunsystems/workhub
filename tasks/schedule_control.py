"""Schedule evidence and deadline outbox; no attendance or intent inference."""
import hashlib
from datetime import datetime,time,timedelta
from zoneinfo import ZoneInfo
from django import forms
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Q,Count
from django.shortcuts import render,redirect,get_object_or_404
from django.template.loader import render_to_string
from django.utils import timezone
from .models import (Task,TaskEvent,Employee,ScheduleRegistration,ScheduleOutcome,
    ScheduleExpectation,DeadlinePolicy,DeadlineDelivery,WorkNotification)
from .permissions import (profile,visible_tasks,visible_employees,can_view_company,
    is_company_admin,is_manager)

SEOUL=ZoneInfo('Asia/Seoul')
PLAN_FIELDS=['start_date','start_time','due_date','activity_start','activity_end']


def observe_schedule(task,kind,extra):
    if task.status=='draft':return {}
    before=extra.get('before') or {}
    current={key:str(getattr(task,key)) for key in PLAN_FIELDS}
    if kind=='created' or before.get('status')=='draft':
        now=timezone.now();local=timezone.localtime(now,SEOUL)
        # Generic start_time defaults to the time the form was opened. Time spent
        # writing a task is not evidence of late registration. Explicit activity
        # times (meetings/visits) retain their separate five-minute tolerance.
        late=(task.activity_start<now-timedelta(minutes=5) if task.activity_start else task.start_date<local.date())
        ScheduleRegistration.objects.get_or_create(task=task,defaults={'registered_at':now,'initial_plan':current,'late':late})
    changes={k:{'before':before[k],'after':current[k],'label':{'start_date':'시작일','start_time':'시작 시간','due_date':'마감일','activity_start':'수행 시작','activity_end':'수행 종료'}[k]} for k in PLAN_FIELDS if k in before and before[k]!=current[k]}
    if changes:
        from django.utils.dateparse import parse_datetime,parse_date
        old_time=parse_datetime(before.get('activity_start',''))
        old_day=timezone.localtime(old_time,SEOUL).date() if old_time else parse_date(before.get('start_date',''))
        return {'schedule_change':True,'schedule_changes':changes,'schedule_urgent':bool(old_day and old_day<=timezone.localdate())}
    return {}


def managing(user):
    employee=profile(user)
    return can_view_company(user) or is_manager(user) or bool(employee and employee.position=='leader')


def scope(user):
    tasks=visible_tasks(user).exclude(status='draft')
    if not managing(user):tasks=tasks.filter(Q(assignee__user=user)|Q(participants__user=user)).distinct()
    return tasks


class OutcomeForm(forms.ModelForm):
    class Meta:
        model=ScheduleOutcome
        fields=['result','actual_date','reason']
        labels={'result':'실시 결과 · 설명 구분','actual_date':'실제 실시일 (참석·실시 완료 시)','reason':'결과·등록 지연·변경에 대한 설명'}
        widgets={'actual_date':forms.DateInput(attrs={'type':'date'}),'reason':forms.Textarea(attrs={'rows':4})}
    def clean(self):
        data=super().clean()
        if not (data.get('reason') or '').strip():self.add_error('reason','내용과 사유를 입력하세요.')
        if data.get('result')=='attended' and not data.get('actual_date'):self.add_error('actual_date','실제 실시일을 입력하세요.')
        if data.get('actual_date') and data['actual_date']>timezone.localdate():self.add_error('actual_date','실제 실시일은 미래 날짜일 수 없습니다.')
        return data


@login_required
@transaction.atomic
def outcome_view(request,pk):
    task=get_object_or_404(scope(request.user).select_for_update(),pk=pk)
    employee=profile(request.user)
    if not employee or not (task.assignee_id==employee.pk or task.participants.filter(pk=employee.pk).exists()):raise PermissionDenied
    existing=ScheduleOutcome.objects.filter(task=task,employee=employee).first()
    form=OutcomeForm(request.POST if request.method=='POST' else None,instance=existing)
    if request.method=='POST' and form.is_valid():
        obj=form.save(commit=False);obj.task=task;obj.employee=employee
        obj.reviewer=None;obj.reviewed_at=None;obj.review_note='';obj.save()
        from .workflow import record
        record(task,request.user,'comment','일정 실시 결과·사유: '+obj.get_result_display()+' / '+obj.reason,{'schedule_outcome':{'employee_id':employee.pk,'result':obj.result,'actual_date':str(obj.actual_date)}})
        messages.success(request,'실시 결과와 설명을 기록했습니다. 업무 완료·결재 상태는 변경되지 않습니다.')
        return redirect('schedule_control')
    return render(request,'tasks/schedule_control_form.html',{'form':form,'title':'일정 실시 결과 · 등록 사유','task':task})


@login_required
@transaction.atomic
def review_outcome(request,pk):
    outcome=get_object_or_404(ScheduleOutcome.objects.select_for_update().select_related('task','employee'),pk=pk,task__in=scope(request.user))
    if not managing(request.user) or outcome.employee.user_id==request.user.pk or not visible_employees(request.user).filter(pk=outcome.employee_id).exists():raise PermissionDenied
    class ReviewForm(forms.Form):
        note=forms.CharField(label='확인 의견 · 보완 요청',max_length=3000,widget=forms.Textarea)
    form=ReviewForm(request.POST if request.method=='POST' else None)
    if request.method=='POST' and form.is_valid():
        outcome.reviewer=request.user;outcome.review_note=form.cleaned_data['note'];outcome.reviewed_at=timezone.now();outcome.save()
        from .workflow import record
        record(outcome.task,request.user,'comment','일정 결과·사유 확인 의견: '+outcome.review_note,{'schedule_outcome_review':outcome.pk})
        return redirect('schedule_control')
    return render(request,'tasks/schedule_control_form.html',{'form':form,'title':'일정 결과 · 사유 확인','task':outcome.task})


class ExpectationForm(forms.ModelForm):
    class Meta:
        model=ScheduleExpectation
        fields=['title','employee','expected_date','register_by']
        labels={'title':'등록해야 할 일정명','employee':'등록 담당자','expected_date':'예정 실시일','register_by':'일정 등록 기한 (한국 시간)'}
        widgets={'expected_date':forms.DateInput(attrs={'type':'date'}),'register_by':forms.DateTimeInput(attrs={'type':'datetime-local'})}
    def __init__(self,*args,user,**kwargs):
        super().__init__(*args,**kwargs)
        self.fields['employee'].queryset=visible_employees(user).filter(is_active=True,user__is_active=True)
    def clean(self):
        data=super().clean()
        if data.get('register_by') and data.get('expected_date') and timezone.localtime(data['register_by']).date()>data['expected_date']:
            self.add_error('register_by','등록 기한은 예정 실시일보다 늦을 수 없습니다.')
        return data


@login_required
def expectation_new(request):
    if not managing(request.user):raise PermissionDenied
    form=ExpectationForm(request.POST if request.method=='POST' else None,user=request.user)
    if request.method=='POST' and form.is_valid():
        obj=form.save(commit=False);obj.creator=request.user;obj.save()
        from .audit import write
        write('schedule_expected','ScheduleExpectation',obj.pk,obj.title,after={'employee_id':obj.employee_id,'register_by':str(obj.register_by)},actor=request.user)
        return redirect('schedule_control')
    return render(request,'tasks/schedule_control_form.html',{'form':form,'title':'일정 등록 대상 지정'})


def expectations(user):
    employees=visible_employees(user) if managing(user) else Employee.objects.filter(user=user)
    return ScheduleExpectation.objects.filter(employee__in=employees).select_related('employee','task')


@login_required
@transaction.atomic
def expectation_resolve(request,pk):
    obj=get_object_or_404(expectations(request.user).select_for_update(),pk=pk)
    class ResolveForm(forms.Form):
        task=forms.ModelChoiceField(label='등록한 업무 연결 (일정 면제 시 비워 두세요)',queryset=scope(request.user).filter(Q(assignee=obj.employee)|Q(participants=obj.employee)).distinct(),required=False)
        reason=forms.CharField(label='연결·등록 면제 사유',max_length=3000,widget=forms.Textarea)
    form=ResolveForm(request.POST if request.method=='POST' else None)
    if request.method=='POST' and form.is_valid():
        if not form.cleaned_data['task'] and not managing(request.user):form.add_error('task','등록 면제는 관리자·팀장의 확인이 필요합니다.')
        else:
            obj.task=form.cleaned_data['task'];obj.closed_reason=form.cleaned_data['reason'];obj.save()
            from .audit import write
            write('schedule_expected_resolved','ScheduleExpectation',obj.pk,obj.title,after={'task_id':obj.task_id,'reason':obj.closed_reason},actor=request.user)
            return redirect('schedule_control')
    return render(request,'tasks/schedule_control_form.html',{'form':form,'title':'등록 일정 연결 · 면제','expectation':obj})


class PolicyForm(forms.ModelForm):
    class Meta:
        model=DeadlinePolicy
        fields=['enabled','recipient_employee','advance_days','send_hour','escalation_days','send_email','send_messenger','weekdays_only']
        labels={'enabled':'마감 알림 사용','recipient_employee':'알림 수신 대상 직원 (비우면 전체)','advance_days':'마감 며칠 전 사전 알림','send_hour':'알림 시작 시각 (한국 시간 · 0~23시)','escalation_days':'지연 며칠 후 직속 상위자 안내','send_email':'일일 요약 이메일 발송','send_messenger':'하이웍스 메신저도 발송','weekdays_only':'토·일요일은 알림 발송 보류'}
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        self.fields['recipient_employee'].queryset=Employee.objects.filter(is_active=True,user__is_active=True)


@login_required
@transaction.atomic
def policy_view(request):
    if not is_company_admin(request.user):raise PermissionDenied
    policy=DeadlinePolicy.objects.filter(pk=1).first() or DeadlinePolicy(pk=1)
    old_enabled=policy.enabled
    form=PolicyForm(request.POST if request.method=='POST' else None,instance=policy)
    if request.method=='POST' and form.is_valid():
        obj=form.save(commit=False)
        if obj.enabled and not old_enabled:obj.activated_at=timezone.now()
        obj.save()
        from .audit import write
        write('deadline_policy','DeadlinePolicy',1,'마감 알림 정책',after={k:str(v) for k,v in form.cleaned_data.items()},actor=request.user)
        messages.success(request,'알림 정책을 저장했습니다. 사용 설정 시 다음 예약 처리부터 적용됩니다.')
        return redirect('deadline_policy')
    return render(request,'tasks/schedule_control_form.html',{'form':form,'title':'마감 알림 설정','policy_page':True})


@login_required
def control_view(request):
    tasks=scope(request.user)
    today=timezone.localdate()
    changes=TaskEvent.objects.filter(task__in=tasks,snapshot__schedule_change=True)
    return render(request,'tasks/schedule_control.html',{'late':ScheduleRegistration.objects.filter(task__in=tasks,late=True).select_related('task')[:100],
        'change_counts':changes.filter(created_at__gte=timezone.now()-timedelta(days=30)).values('author_name').annotate(total=Count('pk'),urgent=Count('pk',filter=Q(snapshot__schedule_urgent=True))).order_by('-total')[:100],
        'changes':TaskEvent.objects.filter(task__in=tasks,snapshot__schedule_change=True).select_related('task')[:100],
        'outcomes':ScheduleOutcome.objects.filter(task__in=tasks).select_related('task','employee','reviewer').order_by('-updated_at')[:100],
        'expectations':expectations(request.user)[:100],'now':timezone.now(),
        'pending_outcomes':tasks.filter(management_mode='event',due_date__lt=today,status__in=['todo','doing','blocked']).order_by('due_date')[:100],
        'deliveries':DeadlineDelivery.objects.filter(task__in=tasks).filter(Q(user=request.user) if not managing(request.user) else Q()).select_related('task','user').order_by('-created_at')[:100],
        'manager':managing(request.user),'company_admin':is_company_admin(request.user),'policy':DeadlinePolicy.objects.filter(pk=1).first()})


def deadline_state(task):
    if task.status in ['draft','done','cancelled']:return None
    if task.status=='review':
        started=task.review_started_at or task.submitted_at
        if not started:return None
        due=timezone.localtime(started,SEOUL).date()+timedelta(days=task.review_days)
        return 'review',due,str(task.report_round)+':'+started.isoformat()
    return 'work',task.due_date,task.due_date.isoformat()


def target_users(task,phase,escalate=False):
    if phase=='review':
        step=task.approval_steps.exclude(decision='approved').first()
        ids=set(task.approval_steps.filter(stage=step.stage).exclude(decision='approved').values_list('approver_id',flat=True)) if step else set()
    else:
        ids=set(task.assignments.filter(completed_at__isnull=True,employee__is_active=True).values_list('employee__user_id',flat=True))
        if task.workflow_version==1 or not task.assignments.exists():ids.add(task.assignee.user_id)
    if escalate:
        supervisors=Employee.objects.filter(user_id__in=ids,is_active=True,supervisor__is_active=True).values_list('supervisor__user_id',flat=True)
        ids=set(supervisors)
    from django.contrib.auth import get_user_model
    from accounts.lifecycle import account_allowed
    return [u for u in get_user_model().objects.filter(pk__in=ids,is_active=True) if account_allowed(u) and visible_tasks(u).filter(pk=task.pk).exists()]


def candidates(now,policy):
    local=timezone.localtime(now,SEOUL);day=local.date()
    if local.hour<policy.send_hour or (policy.weekdays_only and local.weekday()>4):return
    for task in Task.objects.exclude(status__in=['draft','done','cancelled']).select_related('assignee__user').prefetch_related('assignments','approval_steps'):
        state=deadline_state(task)
        if not state:continue
        phase,due,baseline=state;delta=(due-day).days
        if delta==policy.advance_days:kind='advance'
        elif delta==0:kind='due'
        elif delta<0:kind='overdue'
        else:continue
        types=[kind]+(['escalation'] if delta<=-policy.escalation_days else [])
        for kind in types:
            for user in target_users(task,phase,kind=='escalation'):
                if policy.recipient_employee_id and policy.recipient_employee.user_id!=user.pk:continue
                label='결재 검토' if phase=='review' else '업무 수행'
                text={'advance':f'{label} 기한 {policy.advance_days}일 전','due':f'{label} 기한 당일','overdue':f'{label} 기한 {abs(delta)}일 지연','escalation':f'직속 구성원 {label} 기한 {abs(delta)}일 지연'}[kind]
                yield task,user,kind,phase,due,baseline,(text+' · '+task.title)[:240]


@transaction.atomic
def queue_deadlines(now=None):
    now=now or timezone.now();policy=DeadlinePolicy.objects.filter(pk=1,enabled=True).first()
    if not policy:return 0
    count=0;day=timezone.localtime(now,SEOUL).date()
    for task,user,kind,phase,due,baseline,message in candidates(now,policy):
        key=hashlib.sha256(f'{task.pk}:{user.pk}:{phase}:{baseline}:{kind}:{day}'.encode()).hexdigest()
        row,new=DeadlineDelivery.objects.get_or_create(dedupe_key=key,defaults={'task':task,'user':user,'kind':kind,'phase':phase,'due_date':due,'alert_date':day,'baseline':baseline,'message':message,
            'email_state':'pending' if policy.send_email else 'off','messenger_state':'pending' if policy.send_messenger else 'off'})
        if not new:continue
        row.notification=WorkNotification.objects.create(user=user,task=task,message=message)
        row.save(update_fields=['notification']);count+=1
    return count


def still_valid(row,policy,now):
    state=deadline_state(row.task)
    return bool(policy.enabled and (not policy.recipient_employee_id or policy.recipient_employee.user_id==row.user_id) and state and state==(row.phase,row.due_date,row.baseline) and row.alert_date==timezone.localtime(now,SEOUL).date() and
        row.user_id in {u.pk for u in target_users(row.task,row.phase,row.kind=='escalation')})


def deliver_deadlines(now=None):
    now=now or timezone.now();policy=DeadlinePolicy.objects.filter(pk=1).first()
    if not policy:return
    # Ambiguous network sends are never replayed automatically.
    DeadlineDelivery.objects.filter(email_state='sending',sent_at__lt=now-timedelta(minutes=5)).update(email_state='unknown')
    rows=DeadlineDelivery.objects.filter(Q(email_state='pending')|Q(messenger_state='pending')).select_related('task__assignee__user','user','notification').order_by('pk')[:500]
    groups={}
    for row in rows:
        if not still_valid(row,policy,now):
            DeadlineDelivery.objects.filter(pk=row.pk,email_state='pending').update(email_state='skipped')
            DeadlineDelivery.objects.filter(pk=row.pk,messenger_state='pending').update(messenger_state='skipped')
            continue
        if row.messenger_state=='pending':
            if policy.send_messenger and row.notification:
                from .hiworks import enqueue
                enqueue(row.notification)
            from .models import HiworksDelivery
            row.messenger_state='queued' if policy.send_messenger and HiworksDelivery.objects.filter(notification=row.notification).exists() else 'skipped';row.save(update_fields=['messenger_state'])
        if row.email_state=='pending':groups.setdefault(row.user_id,[]).append(row)
    from accounts.services import deliver
    from .audit import write
    for user_id,items in list(groups.items())[:20]:
        user=items[0].user;access=getattr(user,'workhub_access',None)
        if not policy.send_email or not access or not access.verified_at or access.email.lower()!=user.email.lower():
            DeadlineDelivery.objects.filter(pk__in=[x.pk for x in items],email_state='pending').update(email_state='skipped');continue
        # A single daily summary per person. Later items wait until the next day.
        if DeadlineDelivery.objects.filter(user_id=user_id,alert_date=items[0].alert_date,email_state__in=['sent','sending','unknown']).exists():continue
        ids=[x.pk for x in items];claimed=[]
        for pk in ids:
            if DeadlineDelivery.objects.filter(pk=pk,email_state='pending').update(email_state='sending',sent_at=now):claimed.append(pk)
        if not claimed:continue
        items=[x for x in items if x.pk in claimed]
        context={'items':[{'message':x.message,'url':settings.WORKHUB_BASE_URL.rstrip('/')+f'/tasks/{x.task_id}/'} for x in items]}
        try:
            deliver('[Workhub] 마감·지연 업무 안내','\n'.join(x['message']+'\n'+x['url'] for x in context['items']),access.email,html_body=render_to_string('tasks/email_deadline.html',context))
        except Exception:state='unknown'
        else:state='sent'
        DeadlineDelivery.objects.filter(pk__in=claimed).update(email_state=state)
        write('deadline_'+state,'DeadlineDelivery',claimed[0],'마감 알림 일일 요약',after={'count':len(claimed),'state':state})
