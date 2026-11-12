"""Explicit official-work participants and sequential approvals; legacy work is unchanged."""
import secrets
from django import forms
from django.contrib import messages
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
from .permissions import visible_tasks, is_company_admin, profile, can_edit, can_report, can_review,visible_requests,is_manager,can_override_work
from .workflow import record, snapshot


def users():
    return get_user_model().objects.filter(is_active=True).filter(Q(is_superuser=True) | Q(employee__is_active=True)).distinct()


def user_name(user):
    employee = getattr(user, 'employee', None)
    return f'{employee.name} · {employee.department} · {employee.get_position_display()}' if employee else user.get_username()


def refresh_participation_progress(task):
    """Keep the displayed active-work progress consistent with current performers."""
    if task.status not in ['todo', 'doing', 'blocked']:
        return
    rows = list(task.assignments.values_list('progress', 'accepted_at'))
    task.progress = min(99, round(sum(row[0] for row in rows) / len(rows))) if rows else 0
    if any(row[1] is not None for row in rows):
        task.progress = max(1, task.progress)


class UserChoice(forms.ModelChoiceField):
    def label_from_instance(self, obj): return user_name(obj)


class OfficialTaskForm(TaskForm):
    attachment=forms.FileField(widget=forms.ClearableFileInput(attrs={'data-upload-limit':'20971520','accept':'.pdf,.doc,.docx,.xls,.xlsx,.ppt,.pptx,.txt,.csv,.png,.jpg,.jpeg,.zip'}),label='참고 파일 첨부 · 최대 20MB',required=False,
        help_text='소개자료·업무 요청서 등을 함께 등록합니다. 추가 자료는 업무 상세에서 관리하세요.')
    purpose=forms.ChoiceField(choices=[('self','내 업무'),('request','업무 요청')],required=False,widget=forms.HiddenInput)
    intake_revision=forms.IntegerField(required=False,widget=forms.HiddenInput)
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

    def clean_attachment(self):
        value=self.cleaned_data.get('attachment')
        if value:
            from .documents import validate_file
            from .drive_storage import configured
            validate_file(value)
            if not configured():raise ValidationError('자료 저장소 연결 후 파일을 첨부할 수 있습니다.')
        return value

    def __init__(self, *args, user=None, request_intake=None, **kwargs):
        self.request_intake=request_intake
        super().__init__(*args, user=user, **kwargs)
        if not self.instance.pk and not request_intake:
            self.fields['management_mode'].choices=list(self.fields['management_mode'].choices)+[('quick','간단 업무 · 전용 입력 화면')]
        active = Employee.objects.filter(is_active=True).filter(Q(user__isnull=True)|Q(user__is_active=True)).select_related('department')
        self.fields['assignee'].queryset = active
        self.fields['assignee'].label = '총괄 담당자'
        self.fields['collaborators'].queryset = active
        self.fields['observers'].queryset = users()
        self.fields['observers'].label_from_instance = user_name
        self.fields['status'].choices = [('draft','초안'),('todo','예정'),('doing','진행 중'),('blocked','보류')]
        for name in ['start_date','start_time','due_date','report_days','review_days']:
            self.fields[name].disabled = False
        for n in range(1,6): self.fields[f'approver_{n}'].queryset = users()
        self.fields['allocation'].initial = 'shared'
        if request_intake:
            self.fields['assignee'].queryset=active.filter(department_id=request_intake.department_id)
            # The requester may link a sales-owned project that the receiving team did not previously see.
            self.fields['project'].queryset=self.fields['project'].queryset.model.objects.filter(pk=request_intake.project_id)
            self.fields['project'].disabled=True
            self.fields['allocation'].disabled=True
            self.fields['status'].choices=[('todo','예정'),('doing','진행 중')]
        if self.instance.pk:self.initial['purpose']='request' if self.instance.creation_kind=='request' else 'self'

        if self.instance.pk:
            self.fields['teams'].disabled = True
            self.fields['allocation'].disabled = True
            self.initial.update(collaborators=list(self.instance.participants.values_list('pk',flat=True)),
                observers=list(self.instance.watchers.values_list('pk',flat=True)), revision=self.instance.revision,
                no_approval=not self.instance.approval_steps.exists(), allocation='shared')
            for n,step in enumerate(self.instance.approval_steps.all(),1):
                self.initial[f'approver_{n}'] = step.approver_id
                self.initial[f'stage_{n}'] = step.stage

    def clean_management_mode(self):
        value=self.cleaned_data['management_mode']
        if value=='quick':raise ValidationError('간단 업무는 상단의 간단 업무 전달 링크에서 등록하세요.')
        return value

    def clean(self):
        data = super().clean()
        members = set(data.get('collaborators', Employee.objects.none()).values_list('pk',flat=True))
        members.update(Employee.objects.filter(department__in=data.get('teams', []), is_active=True, role='employee', position='member')
                       .filter(Q(user__isnull=True)|Q(user__is_active=True)).values_list('pk',flat=True))
        if data.get('assignee'): members.add(data['assignee'].pk)
        reviewers = [data.get(f'approver_{n}') for n in range(1,6) if data.get(f'approver_{n}')]
        if data.get('management_mode') == 'event':
            data['no_approval'] = True
            if reviewers: self.add_error('management_mode','일반 업무에는 결재자를 지정하지 않습니다. 보고 업무로 선택하거나 결재자를 비워 주세요.')
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
        if self.request_intake:
            if (data.get('intake_revision') or 0)!=self.request_intake.revision:self.add_error(None,'요청 내용이 변경되었습니다. 최신 요청을 확인하세요.')
            if len(members)<self.request_intake.headcount and not (data.get('change_reason') or '').strip():self.add_error('change_reason','요청 인원보다 적게 배정하는 사유를 입력하세요.')
            if self.request_intake.request_kind!='legacy' and (data.get('start_date')!=self.request_intake.requested_start_date or data.get('due_date')!=self.request_intake.requested_due_date):
                if not (data.get('change_reason') or '').strip():self.add_error('change_reason','희망 투입 기간과 다르게 배정하는 사유를 입력하세요.')
        return data


def request_workload(user,intake,form):
    start=form.cleaned_data.get('start_date') if hasattr(form,'cleaned_data') else None
    end=form.cleaned_data.get('due_date') if hasattr(form,'cleaned_data') else None
    start=start or intake.requested_start_date or timezone.localdate();end=end or intake.requested_due_date
    employees=Employee.objects.filter(department_id=intake.department_id,is_active=True).select_related('user')
    rows=[]
    for emp in employees:
        tasks=visible_tasks(user).filter(Q(assignee=emp)|Q(participants=emp),start_date__lte=end,due_date__gte=start).exclude(status__in=['done','cancelled','draft']).distinct()
        rows.append({'employee':emp,'count':tasks.count(),'tasks':list(tasks[:4])})
    return rows[:100]


def notify(task, message, external=True,comment_event=None):
    ids = set(task.participants.exclude(user=None).values_list('user_id',flat=True))
    if task.assignee.user_id:ids.add(task.assignee.user_id)
    ids.update(task.watchers.values_list('pk',flat=True))
    ids.update(task.approval_steps.exclude(approver=None).values_list('approver_id',flat=True))
    if task.creator_id: ids.add(task.creator_id)
    from .hiworks import enqueue
    if comment_event:ids.discard(comment_event.author_id)
    for pk in ids:
        notification = WorkNotification.objects.create(user_id=pk,task=task,message=message[:240])
        if external: enqueue(notification)
        if comment_event:
            from .assignment_mail import enqueue_comment
            enqueue_comment(comment_event,notification)


@login_required
@transaction.atomic
def edit_task(request, task=None, intake_override=None,calendar_prefill=None,on_created=None):
    if not is_company_admin(request.user) and not profile(request.user): raise PermissionDenied
    if task and (not can_edit(request.user,task) or task.status in ['review','done','cancelled']): raise PermissionDenied
    before = snapshot(task) if task else None
    purpose=request.POST.get('purpose',request.GET.get('purpose','self'))
    if purpose not in ['self','request']:purpose='self'
    initial={'project':request.GET.get('project'),'purpose':purpose}
    if purpose=='self' and profile(request.user):initial['assignee']=profile(request.user).pk
    if calendar_prefill and task is None and not intake_override:initial.update(calendar_prefill)
    intake_id=intake_override.pk if intake_override else (request.POST.get('intake') if request.method=='POST' else request.GET.get('intake'))
    intake=None
    if intake_id:
        if not str(intake_id).isdigit(): raise Http404
        if task: raise PermissionDenied
        intake=get_object_or_404(visible_requests(request.user).select_for_update(),pk=intake_id)
        if intake.task_id: return redirect('task_detail',pk=intake.task_id)
        from .requests import can_assign
        if not can_assign(request.user,intake):raise PermissionDenied
        if intake.closed_at or intake.stage=='rejected': raise PermissionDenied
        initial.update(intake=intake.pk,title=intake.title,description=intake.description,completion_criteria=intake.description,
                       due_date=intake.requested_due_date,start_date=intake.requested_start_date or timezone.localdate(),project=intake.project_id,
                       customer=intake.customer,solution=intake.solution,location=intake.location,intake_revision=intake.revision,purpose='request')
    form = OfficialTaskForm(request.POST if request.method=='POST' else None, request.FILES or None, instance=task, user=request.user,initial=initial,request_intake=intake)
    if calendar_prefill and not form.is_bound and calendar_prefill.get('start_time') is None:form.initial['start_time']=None
    if request.method=='POST' and form.is_valid():
        if intake:
            from .requests import claim
            try:claim(intake,form.cleaned_data.get('intake_revision') or 0)
            except ValidationError as exc:
                form.add_error(None,exc)
                return render(request,'tasks/official_form.html',{'form':form,'task':task,'intake':intake})
        obj = form.save(commit=False)
        is_new = not obj.pk
        obj.workflow_version = 2
        if is_new:
            obj.creator = request.user
            obj.creation_kind='cooperation' if intake else form.cleaned_data.get('purpose') or 'self'
        obj.revision += 1
        obj.save()
        reference=None
        if form.cleaned_data.get('attachment'):
            from .documents import add_document
            from .drive_storage import RepositoryError
            try:reference=add_document(request.user,'task',obj,{'file':form.cleaned_data['attachment'],'kind':'reference'})
            except RepositoryError as exc:
                form.add_error('attachment',str(exc))
                response=render(request,'tasks/official_form.html',{'form':form,'task':task,'intake':intake})
                transaction.set_rollback(True)
                return response
        if intake:
            intake.task=obj; intake.assigned_at=timezone.now(); intake.save(update_fields=['task','assigned_at','revision'])
        members = form.cleaned_data['member_ids']
        # Preserve removed participants' progress in the immutable event before detaching them.
        prior = list(obj.assignments.values('employee_id','progress','accepted_at','completed_at'))
        prior = [{k:str(v) for k,v in row.items()} for row in prior]
        obj.assignments.exclude(employee_id__in=members).delete()
        for pk in members: TaskParticipation.objects.get_or_create(task=obj,employee_id=pk)
        obj.watchers.set(form.cleaned_data['observers'])
        if intake and intake.creator_id: obj.watchers.add(intake.creator_id)
        if intake:
            from .models import RequestAllocation
            from .requests import event
            for member in members:RequestAllocation.objects.create(request=intake,employee_id=member,role='총괄 담당' if member==obj.assignee_id else '수행 담당',start_date=obj.start_date,end_date=obj.due_date)
            event(intake,request.user,'인력 배정','수행 업무로 연결했습니다.',{'participants':list(members),'start':str(obj.start_date),'end':str(obj.due_date)})
        old_line = list(obj.approval_steps.values('approver_name','position','stage','decision'))
        obj.approval_steps.all().delete()
        ApprovalStep.objects.bulk_create([ApprovalStep(task=obj,approver=u,approver_name=user_name(u),position=n,stage=form.cleaned_data['stages'][n-1])
                                         for n,u in enumerate(form.cleaned_data['reviewers'],1)])
        if task and before['assignee_id'] != str(obj.assignee_id):
            obj.accepted_at=None; obj.last_report_at=None; obj.assigned_at=timezone.now(); obj.save(update_fields=['accepted_at','last_report_at','assigned_at'])
            obj.assignments.filter(employee=obj.assignee).update(accepted_at=None,progress=0,completed_at=None,assigned_at=obj.assigned_at)
        if task:
            refresh_participation_progress(obj)
            obj.save(update_fields=['progress'])
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
                if reference:
                    from .models import WorkDocument
                    WorkDocument.objects.create(task=clone,file=reference.file.name,name=reference.name,kind=reference.kind,
                        size=reference.size,uploader=request.user,uploader_name=reference.uploader_name)
                TaskParticipation.objects.create(task=clone,employee_id=pk)
                clone.watchers.set(obj.watchers.all())
                ApprovalStep.objects.bulk_create([ApprovalStep(task=clone,approver=s.approver,approver_name=s.approver_name,position=s.position,stage=s.stage) for s in obj.approval_steps.all()])
                record(clone,request.user,'created','직원별 개별 배정',{'group_key':key})
                if clone.status!='draft': notify(clone,'새 업무가 배정되었습니다.',external=clone.assignment_messenger)
                from .assignment_mail import enqueue as enqueue_assignment_mail
                enqueue_assignment_mail(clone,request.user)
            obj.assignments.exclude(employee_id=obj.assignee_id).delete()
        if obj.status!='draft': notify(obj,'업무가 등록되거나 변경되었습니다. 내용을 확인해 주세요.',external=obj.assignment_messenger)
        from .assignment_mail import enqueue as enqueue_assignment_mail
        previous_ids={int(row['employee_id']) for row in prior}
        if before: previous_ids.add(int(before['assignee_id']))
        current_ids=set(obj.participants.values_list('pk',flat=True)) | {obj.assignee_id}
        new_ids=current_ids if is_new or (before and before['status']=='draft') else current_ids-previous_ids
        enqueue_assignment_mail(obj,request.user,new_ids)
        if reference:messages.success(request,'참고 파일 업로드가 완료되었습니다.',extra_tags='upload-success')
        if on_created and is_new:on_created(obj)
        return redirect('task_detail',pk=obj.pk)
    return render(request,'tasks/official_form.html',{'form':form,'task':task,'intake':intake,'calendar_details':getattr(request,'_calendar_quick',False),'workload':request_workload(request.user,intake,form) if intake else []})


class ActionForm(forms.Form):
    revision = forms.IntegerField(widget=forms.HiddenInput)
    body = forms.CharField(label='내용 · 사유', max_length=10000, widget=forms.Textarea(attrs={'rows':5}))
    progress = forms.IntegerField(label='내 진행률 (%)', min_value=0,max_value=99,required=False)
    quality_score = forms.IntegerField(label='최종 결과 평가 (선택 · 0~100)',min_value=0,max_value=100,required=False)
    decision = forms.ChoiceField(label='처리',required=False,choices=[('','선택'),('approve','승인'),('return','보완 요청 · 반려'),('hold','보류'),('cancel','업무 취소'),('reopen','완료 업무 재개')])
    replacement = UserChoice(label='인계받을 담당자 · 대결자',queryset=get_user_model().objects.none(),required=False)


@login_required
def action(request,pk,kind):
    titles={'accept':'배정 확인','progress':'진행 보고','finish':'업무 수행 완료','submit':'완료 보고 제출','review':'결재 검토',
            'comment':'질문 · 의견','override':'대표 직권 처리','cancel':'업무 취소','recall':'완료 보고 회수',
            'delegate':'결재 인계 · 대결','handover':'총괄 담당자 인계'}
    if kind not in titles: raise PermissionDenied
    task=get_object_or_404(visible_tasks(request.user),pk=pk,workflow_version=2)
    if kind=='submit': titles['submit']='완료 보고 제출' if task.approval_steps.exists() else '업무 완료'
    from .progress_reporting import next_report_number
    participant=task.assignments.filter(employee__user=request.user).first()
    progress_initial=min(99,participant.progress) if participant else min(99,task.progress)
    if kind=='progress': titles['progress']=f'진행 보고 {next_report_number(task,request.user)}차'
    form=ActionForm(request.POST if request.method=='POST' else None,initial={'revision':task.revision,'progress':progress_initial,**({'body':'업무 내용과 기한을 확인했습니다.'} if kind=='accept' else {})})
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
    return render(request,'tasks/action.html',{'task':task,'form':form,'title':titles[kind],'hint':'업무 내용과 기한을 확인한 뒤 저장하세요. 배정 확인 후 진행률이 최소 1%로 표시되고 진행 보고·완료 처리가 가능합니다.' if kind=='accept' else ''})


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
    executive=can_override_work(user)
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
        if kind == 'accept':
            if participant.accepted_at: raise ValidationError('이미 배정 확인한 업무입니다.')
            participant.accepted_at=timezone.now()
            participant.progress=max(participant.progress,1)
            event='accepted'
        elif not participant.accepted_at:
            raise ValidationError('업무 내용과 기한을 확인하고 먼저 배정 확인을 완료하세요.')
        if kind=='accept': pass
        elif kind=='progress':
            if data.get('progress') is None: raise ValidationError('진행률을 입력하세요.')
            participant.progress=data['progress']; participant.completed_at=None; event='progress'
        elif kind=='finish':
            participant.progress=100; participant.completed_at=timezone.now(); event='progress'
        else:
            if task.assignee.user_id!=user.pk: raise PermissionDenied
            if task.status=='blocked': raise ValidationError('보류 중인 업무는 업무 책임자가 보류를 해제한 뒤 완료 보고하세요.')
            if task.assignments.exclude(pk=participant.pk).filter(completed_at__isnull=True).exists():
                raise ValidationError('공동 수행자 모두가 업무 수행 완료를 처리한 후 완료 보고를 제출하세요.')
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
        if task.assignments.filter(accepted_at__isnull=False).exists(): task.progress=max(task.progress,1)
        if active and task.status not in ['review','done']:
            task.status='blocked' if task.status=='blocked' else 'doing'
            task.progress=min(task.progress,99)
        if kind != 'accept': task.last_report_at=timezone.now()
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
            if task.participants.filter(user=user).exists() or task.assignee.user_id==user.pk:
                raise ValidationError('본인이 수행한 업무는 직권으로 승인할 수 없습니다. 다른 결재자에게 검토를 요청하세요.')
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
        old=task.assignee; task.assignee=replacement.employee; task.accepted_at=None; task.last_report_at=None; task.assigned_at=timezone.now()
        TaskParticipation.objects.update_or_create(task=task,employee=task.assignee,defaults={'accepted_at':None,'progress':0,'completed_at':None,'assigned_at':task.assigned_at})
        task.assignments.filter(employee=old).delete()
        refresh_participation_progress(task)
        data={**data,'body':f'{old} → {task.assignee}\n'+data['body']}; event='handover'
    else: raise ValidationError('지원하지 않는 처리입니다.')
    task.revision+=1; task.save()
    if kind=='handover':
        from .assignment_mail import enqueue as enqueue_assignment_mail
        enqueue_assignment_mail(task,user,{task.assignee_id})
    from .progress_reporting import next_report_number
    report_details={'progress_report_number':next_report_number(task,user),'performer_progress':participant.progress} if kind=='progress' else {}
    saved_event=record(task,user,event,data['body'],{'before':before,'action':kind,'decision':data.get('decision'),
           'round':task.report_round,'step':current.position if current else None,**report_details})
    if task.status=='done':
        from .work_series import completion_followup
        completion_followup(task)
    notify(task,('새 의견: ' if kind=='comment' else '대표 직권 처리: ' if kind=='override' else '')+data['body'][:150], external=(task.assignment_messenger if kind in ['handover','comment'] else kind in ['submit','review','override','delegate','cancel']),comment_event=saved_event if kind=='comment' else None)
    return task
