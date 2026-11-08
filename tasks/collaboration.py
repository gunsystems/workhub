"""Explicit official-work participants and sequential approvals; legacy work is unchanged."""
import secrets
from django import forms
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction,OperationalError
from django.db.models import Q,F
from django.shortcuts import get_object_or_404, redirect, render
from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.utils import timezone

from .models import Task, Employee, Department, TaskParticipation, ApprovalStep, WorkNotification,WorkRequest
from .forms import TaskForm
from .permissions import visible_tasks, is_company_admin, profile, can_edit, can_report, can_review,visible_requests,is_manager
from .workflow import record, snapshot


def users():
    return get_user_model().objects.filter(is_active=True).filter(Q(is_superuser=True) | Q(employee__is_active=True)).distinct()


def user_name(user):
    employee = getattr(user, 'employee', None)
    return f'{employee.name} · {employee.department} · {employee.get_position_display()}' if employee else user.get_username()


class UserChoice(forms.ModelChoiceField):
    def label_from_instance(self, obj): return user_name(obj)


class OfficialTaskForm(TaskForm):
    intake = forms.IntegerField(widget=forms.HiddenInput,required=False)
    teams = forms.ModelMultipleChoiceField(label='함께 수행할 팀', queryset=Department.objects.all(), required=False,
        widget=forms.CheckboxSelectMultiple, help_text='팀원 전체를 공동 수행자로 추가합니다. 팀장·본부장은 개별 선택하세요.')
    collaborators = forms.ModelMultipleChoiceField(label='함께 수행할 직원', queryset=Employee.objects.none(), required=False,
        widget=forms.CheckboxSelectMultiple)
    observers = forms.ModelMultipleChoiceField(label='참조자', queryset=get_user_model().objects.none(), required=False,
        widget=forms.CheckboxSelectMultiple, help_text='조회·질문·의견 공유만 하며 승인 책임은 없습니다.')
    allocation = forms.ChoiceField(label='배정 방식', choices=[('shared','공동 업무 · 총괄 담당자가 결과 취합'),('individual','직원별 업무 · 각자 수행하고 보고')])
    no_approval = forms.BooleanField(label='결재 없이 총괄 담당자가 완료 처리', required=False)
    revision = forms.IntegerField(widget=forms.HiddenInput, initial=0)
    for number in range(1, 6):
        locals()[f'approver_{number}'] = UserChoice(label=f'{number}번 결재자', queryset=get_user_model().objects.none(), required=False)
        locals()[f'stage_{number}'] = forms.TypedChoiceField(label=f'{number}번 결재자의 결재 단계', choices=[(n,f'{n}단계') for n in range(1,6)], coerce=int, required=False, initial=number,
            help_text='같은 단계의 결재자는 동시에 검토하고 모두 승인해야 다음 단계로 넘어갑니다.')
    del number

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, user=user, **kwargs)
        active = Employee.objects.filter(is_active=True).filter(Q(user__isnull=True)|Q(user__is_active=True)).select_related('department')
        self.fields['assignee'].queryset = active
        self.fields['assignee'].label = '총괄 담당자'
        self.fields['collaborators'].queryset = active
        self.fields['observers'].queryset = users()
        self.fields['observers'].label_from_instance = user_name
        self.fields['status'].choices = [('draft','초안'),('todo','예정'),('doing','진행 중'),('blocked','보류')]
        for name in ['start_date','due_date','report_days','review_days']:
            self.fields[name].disabled = False
        for n in range(1,6): self.fields[f'approver_{n}'].queryset = users()
        self.fields['allocation'].initial = 'shared'
        if self.instance.pk:
            self.fields['teams'].disabled = True
            self.fields['allocation'].disabled = True
            self.initial.update(collaborators=list(self.instance.participants.values_list('pk',flat=True)),
                observers=list(self.instance.watchers.values_list('pk',flat=True)), revision=self.instance.revision,
                no_approval=not self.instance.approval_steps.exists(), allocation='shared')
            for n,step in enumerate(self.instance.approval_steps.all(),1):
                self.initial[f'approver_{n}'] = step.approver_id
                self.initial[f'stage_{n}'] = step.stage

    def clean(self):
        data = super().clean()
        members = set(data.get('collaborators', Employee.objects.none()).values_list('pk',flat=True))
        members.update(Employee.objects.filter(department__in=data.get('teams', []), is_active=True, role='employee', position='member')
                       .filter(Q(user__isnull=True)|Q(user__is_active=True)).values_list('pk',flat=True))
        if data.get('assignee'): members.add(data['assignee'].pk)
        reviewers = [data.get(f'approver_{n}') for n in range(1,6) if data.get(f'approver_{n}')]
        if not data.get('no_approval') and not reviewers:
            self.add_error('approver_1', '결재자를 선택하거나 결재 없이 완료 처리를 명시하세요.')
        if data.get('no_approval') and reviewers:
            self.add_error('no_approval', '결재자를 지정한 경우 결재 없이 완료할 수 없습니다.')
        if len({u.pk for u in reviewers}) != len(reviewers): self.add_error(None, '결재자는 중복 선택할 수 없습니다.')
        if Employee.objects.filter(pk__in=members, user__in=reviewers).exists():
            self.add_error(None, '본인 수행 업무의 셀프 결재를 막기 위해 수행자와 결재자는 구분해 주세요.')
        if self.instance.pk and data.get('revision') != self.instance.revision:
            self.add_error(None, '다른 변경이 반영되었습니다. 화면을 새로 열어 다시 작성하세요.')
        data['member_ids'], data['reviewers'] = members, reviewers
        data['stages']=[data.get(f'stage_{n}') or n for n in range(1,6) if data.get(f'approver_{n}')]
        return data


def notify(task, message, external=True):
    ids = set(task.participants.exclude(user=None).values_list('user_id',flat=True))
    ids.update(task.watchers.values_list('pk',flat=True))
    ids.update(task.approval_steps.exclude(approver=None).values_list('approver_id',flat=True))
    if task.creator_id: ids.add(task.creator_id)
    from .hiworks import enqueue
    for pk in ids:
        notification = WorkNotification.objects.create(user_id=pk,task=task,message=message[:240])
        if external: enqueue(notification)


@login_required
@transaction.atomic
def edit_task(request, task=None):
    if not is_company_admin(request.user) and not profile(request.user): raise PermissionDenied
    if task and (not can_edit(request.user,task) or task.status in ['review','done','cancelled']): raise PermissionDenied
    before = snapshot(task) if task else None
    initial={'project':request.GET.get('project')}
    intake_id=request.POST.get('intake') if request.method=='POST' else request.GET.get('intake')
    intake=None
    if intake_id:
        if not str(intake_id).isdigit(): raise Http404
        if not is_manager(request.user) or task: raise PermissionDenied
        intake=get_object_or_404(visible_requests(request.user).select_for_update(),pk=intake_id)
        if intake.task_id: return redirect('task_detail',pk=intake.task_id)
        if intake.closed_at: raise PermissionDenied
        initial.update(intake=intake.pk,title=intake.title,description=intake.description,completion_criteria=intake.description,
                       due_date=intake.requested_due_date,start_date=timezone.localdate(),project=intake.project_id)
    form = OfficialTaskForm(request.POST if request.method=='POST' else None, instance=task, user=request.user,initial=initial)
    if request.method=='POST' and form.is_valid():
        obj = form.save(commit=False)
        is_new = not obj.pk
        obj.workflow_version = 2
        if is_new: obj.creator = request.user
        obj.revision += 1
        obj.save()
        if intake:
            intake.task=obj; intake.assigned_at=timezone.now(); intake.save(update_fields=['task','assigned_at'])
        members = form.cleaned_data['member_ids']
        # Preserve removed participants' progress in the immutable event before detaching them.
        prior = list(obj.assignments.values('employee_id','progress','accepted_at','completed_at'))
        prior = [{k:str(v) for k,v in row.items()} for row in prior]
        obj.assignments.exclude(employee_id__in=members).delete()
        for pk in members: TaskParticipation.objects.get_or_create(task=obj,employee_id=pk)
        obj.watchers.set(form.cleaned_data['observers'])
        if intake and intake.creator_id: obj.watchers.add(intake.creator_id)
        old_line = list(obj.approval_steps.values('approver_name','position','stage','decision'))
        obj.approval_steps.all().delete()
        ApprovalStep.objects.bulk_create([ApprovalStep(task=obj,approver=u,approver_name=user_name(u),position=n,stage=form.cleaned_data['stages'][n-1])
                                         for n,u in enumerate(form.cleaned_data['reviewers'],1)])
        if task and before['assignee_id'] != str(obj.assignee_id):
            obj.accepted_at=None; obj.assigned_at=timezone.now(); obj.save(update_fields=['accepted_at','assigned_at'])
        record(obj,request.user,'created' if is_new else 'changed',form.cleaned_data.get('change_reason') or '업무 등록 · 참여자 및 결재라인 설정',
               {'before':before,'previous_participation':prior,'previous_approval_line':old_line,
                'participants':list(members),'approval_line':[user_name(u) for u in form.cleaned_data['reviewers']]})
        if is_new and form.cleaned_data['allocation']=='individual':
            key = secrets.token_hex(16)
            obj.group_key=key; obj.save(update_fields=['group_key'])
            # Each employee gets an independent copy, not a shared approval state.
            for pk in members - {obj.assignee_id}:
                fields = {f.name:getattr(obj,f.name) for f in Task._meta.fields if f.name not in
                          ['id','assignee','revision','accepted_at','last_report_at','submitted_at','completed_at','approved_at','quality_score']}
                fields['assignee_id']=pk
                clone=Task.objects.create(**fields)
                TaskParticipation.objects.create(task=clone,employee_id=pk)
                clone.watchers.set(obj.watchers.all())
                ApprovalStep.objects.bulk_create([ApprovalStep(task=clone,approver=s.approver,approver_name=s.approver_name,position=s.position,stage=s.stage) for s in obj.approval_steps.all()])
                record(clone,request.user,'created','직원별 개별 배정',{'group_key':key})
                if clone.status!='draft': notify(clone,'새 업무가 배정되었습니다.')
            obj.assignments.exclude(employee_id=obj.assignee_id).delete()
        if obj.status!='draft': notify(obj,'업무가 등록되거나 변경되었습니다. 내용을 확인해 주세요.')
        return redirect('task_detail',pk=obj.pk)
    return render(request,'tasks/official_form.html',{'form':form,'task':task})


class ActionForm(forms.Form):
    revision = forms.IntegerField(widget=forms.HiddenInput)
    body = forms.CharField(label='내용 · 사유', max_length=10000, widget=forms.Textarea(attrs={'rows':5}))
    progress = forms.IntegerField(label='내 진행률 (%)', min_value=0,max_value=99,required=False)
    quality_score = forms.IntegerField(label='최종 결과 평가 (선택 · 0~100)',min_value=0,max_value=100,required=False)
    decision = forms.ChoiceField(label='처리',required=False,choices=[('','선택'),('approve','승인'),('return','보완 요청 · 반려'),('hold','보류'),('cancel','업무 취소'),('reopen','완료 업무 재개')])
    replacement = UserChoice(label='인계받을 담당자 · 대결자',queryset=get_user_model().objects.none(),required=False)


@login_required
def action(request,pk,kind):
    titles={'accept':'배정 확인','progress':'진행 보고','finish':'내 수행 완료','submit':'완료 보고 제출','review':'결재 검토',
            'comment':'질문 · 의견','override':'대표 직권 처리','cancel':'업무 취소','recall':'완료 보고 회수',
            'delegate':'결재 인계 · 대결','handover':'총괄 담당자 인계'}
    if kind not in titles: raise PermissionDenied
    task=get_object_or_404(visible_tasks(request.user),pk=pk,workflow_version=2)
    form=ActionForm(request.POST if request.method=='POST' else None,initial={'revision':task.revision})
    form.fields['replacement'].queryset=users().filter(employee__is_active=True)
    if kind!='progress': form.fields.pop('progress')
    if kind not in ['review','override']: form.fields.pop('quality_score')
    if kind not in ['review','override']: form.fields.pop('decision')
    elif kind=='review': form.fields['decision'].choices=[('approve','승인'),('return','보완 요청 · 반려')]
    if kind not in ['delegate','handover']: form.fields.pop('replacement')
    if request.method=='POST' and form.is_valid():
        try: change(request.user,pk,kind,form.cleaned_data)
        except ValidationError as exc: form.add_error(None,exc)
        else: return redirect('task_detail',pk=pk)
    return render(request,'tasks/action.html',{'task':task,'form':form,'title':titles[kind]})


def change(user,pk,kind,data):
    # SQLite uses database locks rather than row locks. Retry a busy snapshot in a
    # fresh transaction; the revision claim then rejects stale concurrent requests.
    for attempt in range(3):
        try: return _change(user,pk,kind,data)
        except OperationalError as exc:
            if 'locked' not in str(exc).lower(): raise
            if attempt==2: raise ValidationError('다른 처리가 진행 중입니다. 잠시 후 화면을 다시 열어 주세요.')
            import time
            time.sleep(.05)


@transaction.atomic
def _change(user,pk,kind,data):
    if not user.is_authenticated or not user.is_active or (not is_company_admin(user) and not profile(user)):
        raise PermissionDenied
    if not data.get('body','').strip(): raise ValidationError('처리 내용과 사유를 입력하세요.')
    task=visible_tasks(user).select_for_update(of=('self',)).get(pk=pk,workflow_version=2)
    if data.get('revision') != task.revision: raise ValidationError('다른 변경이 반영되었습니다. 화면을 다시 열어 확인하세요.')
    if not Task.objects.filter(pk=pk,revision=task.revision).update(revision=F('revision')+1):
        raise ValidationError('다른 변경이 반영되었습니다. 화면을 다시 열어 확인하세요.')
    participant=task.assignments.filter(employee__user=user,employee__is_active=True).first()
    owner=task.creator_id==user.pk
    executive=is_company_admin(user)
    current=task.approval_steps.exclude(decision='approved').first()
    if current and kind in ['review','delegate']:
        current=task.approval_steps.filter(stage=current.stage,approver=user).exclude(decision='approved').first() or current
    before=snapshot(task)
    active=task.status in ['todo','doing','blocked']
    event='comment'
    if kind=='comment':
        if task.status=='draft' and not (owner or executive): raise PermissionDenied
    elif kind in ['accept','progress','finish','submit']:
        if not participant: raise PermissionDenied
        if not active: raise ValidationError('진행 가능한 업무만 수행·보고할 수 있습니다.')
        if not participant.accepted_at: participant.accepted_at=timezone.now()
        if kind=='accept': event='accepted'
        elif kind=='progress':
            if data.get('progress') is None: raise ValidationError('진행률을 입력하세요.')
            participant.progress=data['progress']; participant.completed_at=None; event='progress'
        elif kind=='finish':
            participant.progress=100; participant.completed_at=timezone.now(); event='progress'
        else:
            if task.assignee.user_id!=user.pk: raise PermissionDenied
            if task.status=='blocked': raise ValidationError('보류 중인 업무는 업무 책임자가 보류를 해제한 뒤 완료 보고하세요.')
            if task.assignments.exclude(pk=participant.pk).filter(completed_at__isnull=True).exists():
                raise ValidationError('공동 수행자 모두가 내 수행 완료를 처리한 후 취합 보고하세요.')
            if task.checklist.filter(required=True,checked=False,retired=False).exists(): raise ValidationError('필수 체크리스트를 완료하세요.')
            participant.progress=100; participant.completed_at=timezone.now()
            task.approval_steps.update(decision='pending',decided_at=None)
            task.report_round+=1; task.submitted_at=timezone.now()
            task.review_started_at=task.submitted_at
            task.status='review' if task.approval_steps.exists() else 'done'
            if task.status=='done': task.completed_at=task.submitted_at
            event='submitted'
        participant.save()
        task.progress=round(sum(task.assignments.values_list('progress',flat=True))/task.assignments.count())
        if active and task.status not in ['review','done']:
            task.status='blocked' if task.status=='blocked' else 'doing'
            task.progress=min(task.progress,99)
        task.last_report_at=timezone.now()
        if task.assignee.user_id==user.pk: task.accepted_at=participant.accepted_at
    elif kind=='review':
        if not can_review(user,task): raise PermissionDenied
        decision=data.get('decision')
        if decision not in ['approve','return']: raise ValidationError('승인 또는 반려를 선택하세요.')
        if decision=='approve' and task.checklist.filter(required=True,checked=False,retired=False).exists(): raise ValidationError('필수 체크리스트를 완료하세요.')
        current.decision='approved' if decision=='approve' else 'returned'; current.decided_at=timezone.now(); current.save()
        if decision=='return':
            task.status='doing'; task.submitted_at=None; task.progress=min(task.progress,99); event='returned'
            task.assignments.filter(employee=task.assignee).update(completed_at=None,progress=99)
        else:
            event='approved'
            if not task.approval_steps.filter(stage=current.stage).exclude(decision='approved').exists():
                task.review_started_at=timezone.now()
            if not task.approval_steps.exclude(decision='approved').exists():
                task.status='done'; task.approved_at=timezone.now(); task.completed_at=task.submitted_at; task.progress=100
                task.quality_score=data.get('quality_score')
    elif kind=='override':
        if not executive: raise PermissionDenied
        decision=data.get('decision')
        if decision=='approve':
            if task.status!='review': raise ValidationError('완료 보고가 제출된 업무만 직권 승인할 수 있습니다.')
            if task.checklist.filter(required=True,checked=False,retired=False).exists(): raise ValidationError('필수 체크리스트를 완료하세요.')
            task.status='done'; task.progress=100; task.approved_at=timezone.now(); task.completed_at=task.submitted_at
            task.quality_score=data.get('quality_score')
        elif decision=='return':
            if task.status!='review': raise ValidationError('결재 중인 업무만 반려할 수 있습니다.')
            task.status='doing'; task.progress=min(task.progress,99); task.submitted_at=None
            task.assignments.filter(employee=task.assignee).update(completed_at=None,progress=99)
        elif decision=='hold':
            if not active: raise ValidationError('진행 중인 업무만 보류할 수 있습니다.')
            task.status='blocked'; task.delay_reason=data['body']
        elif decision=='cancel':
            if task.status in ['done','cancelled']: raise ValidationError('이미 종료된 업무입니다.')
            task.status='cancelled'
        elif decision=='reopen':
            if task.status!='done': raise ValidationError('완료된 업무만 재개할 수 있습니다.')
            task.status='doing'; task.progress=0; task.approved_at=None; task.completed_at=None; task.submitted_at=None
            task.approval_steps.update(decision='pending',decided_at=None)
            task.assignments.update(completed_at=None,progress=0)
        else: raise ValidationError('직권 처리 항목을 선택하세요.')
        event='override'
    elif kind=='recall':
        if task.assignee.user_id!=user.pk: raise PermissionDenied
        if task.status!='review': raise ValidationError('결재 중인 보고만 회수할 수 있습니다.')
        task.status='doing'; task.submitted_at=None; task.progress=99; event='returned'
        task.assignments.filter(employee=task.assignee).update(completed_at=None,progress=99)
    elif kind=='cancel':
        if not owner: raise PermissionDenied
        if task.status in ['review','done','cancelled']: raise ValidationError('결재 중에는 회수 후 취소하고, 완료 업무는 대표에게 재개를 요청하세요.')
        task.status='cancelled'; event='changed'
    elif kind=='delegate':
        if not (executive or owner or (current and current.approver_id==user.pk)): raise PermissionDenied
        if task.status!='review' or not current: raise ValidationError('현재 대기 중인 결재만 인계할 수 있습니다.')
        replacement=data.get('replacement')
        if not replacement or not replacement.is_active or not getattr(replacement,'employee',None) or not replacement.employee.is_active: raise ValidationError('사용 가능한 직원을 선택하세요.')
        if task.participants.filter(user=replacement).exists() or task.approval_steps.filter(approver=replacement).exists(): raise ValidationError('수행자 또는 기존 결재자에게 인계할 수 없습니다.')
        old=current.approver_name; current.approver=replacement; current.approver_name=user_name(replacement); current.save()
        data={**data,'body':f'{old} → {current.approver_name}\n'+data['body']}; event='handover'
    elif kind=='handover':
        if not (owner or executive): raise PermissionDenied
        if task.status not in ['todo','doing','blocked']: raise ValidationError('결재·완료 업무는 회수·재개 후 인계하세요.')
        replacement=data.get('replacement')
        if not replacement or not getattr(replacement,'employee',None) or not replacement.employee.is_active or not replacement.is_active: raise ValidationError('인계받을 직원을 선택하세요.')
        if replacement.employee.pk==task.assignee_id: raise ValidationError('현재 담당자와 다른 직원을 선택하세요.')
        if task.approval_steps.filter(approver=replacement).exists(): raise ValidationError('결재자에게 수행 업무를 인계할 수 없습니다.')
        old=task.assignee; task.assignee=replacement.employee; task.accepted_at=None; task.assigned_at=timezone.now()
        TaskParticipation.objects.get_or_create(task=task,employee=task.assignee)
        task.assignments.filter(employee=old).delete()
        data={**data,'body':f'{old} → {task.assignee}\n'+data['body']}; event='handover'
    else: raise ValidationError('지원하지 않는 처리입니다.')
    task.revision+=1; task.save()
    record(task,user,event,data['body'],{'before':before,'action':kind,'decision':data.get('decision'),
           'round':task.report_round,'step':current.position if current else None})
    notify(task,('대표 직권 처리: ' if kind=='override' else '')+data['body'][:150], external=kind in ['submit','review','override','delegate','handover','cancel'])
    return task
