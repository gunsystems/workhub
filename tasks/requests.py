"""Cross-team intake keeps the request, resource decision and work separate."""
import uuid
from functools import wraps
from time import sleep
from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied,ValidationError
from django.db import transaction,OperationalError
from django.db.models import Q,F
from django.shortcuts import get_object_or_404,render,redirect
from django.utils import timezone
from django.views.decorators.http import require_POST
from .models import WorkRequest,RequestEvent,RequestNotification,RequestAllocation,Employee,Department,Task
from .permissions import profile,can_view_company,visible_requests,visible_projects,visible_tasks


def retry_busy(view):
    @wraps(view)
    def wrapped(request,*args,**kwargs):
        for attempt in range(3):
            try:return view(request,*args,**kwargs)
            except OperationalError as exc:
                if 'locked' not in str(exc).lower():raise
                if attempt<2:sleep(.05)
                else:return render(request,'tasks/request_busy.html',status=503)
    return wrapped


def manages_team(user,department_id=None):
    if can_view_company(user):return True
    emp=profile(user)
    return bool(emp and (emp.role=='manager' or emp.position=='leader') and (department_id is None or emp.department_id==department_id))


def can_assign(user,obj):return manages_team(user,obj.department_id)


def recipients(obj):
    users=get_user_model().objects.filter(is_active=True)
    ids=set(users.filter(employee__is_active=True,employee__department_id=obj.department_id).filter(Q(employee__role='manager')|Q(employee__position='leader')).values_list('pk',flat=True))
    if obj.coordinator_id and obj.coordinator.is_active and obj.coordinator.user_id and obj.coordinator.user.is_active:ids.add(obj.coordinator.user_id)
    if not ids:ids.update(users.filter(is_superuser=True).values_list('pk',flat=True))
    return ids


def event(obj,user,action,body,extra=None):
    emp=profile(user)
    e=RequestEvent.objects.create(request=obj,actor=user,actor_name=emp.name if emp else user.get_username(),action=action,body=body,
        snapshot={'stage':obj.stage,'revision':obj.revision,'task_id':obj.task_id,**(extra or {})})
    ids=recipients(obj)
    if obj.creator_id and obj.creator.is_active:ids.add(obj.creator_id)
    if obj.task_id:ids.update(obj.task.participants.filter(is_active=True,user__is_active=True).values_list('user_id',flat=True))
    RequestNotification.objects.bulk_create([RequestNotification(request=obj,event=e,user_id=pk) for pk in ids if pk and pk!=user.pk])
    from .audit import write
    write('request_event','WorkRequest',obj.pk,obj.title,after={'action':action,'body':body,'snapshot':e.snapshot},actor=user)
    return e


class TeamEmployeeSelect(forms.Select):
    def create_option(self,name,value,label,selected,index,subindex=None,attrs=None):
        option=super().create_option(name,value,label,selected,index,subindex=subindex,attrs=attrs)
        if value and hasattr(value,'instance'):option['attrs']['data-team']=value.instance.department_id
        return option


class RequestForm(forms.ModelForm):
    submission=forms.UUIDField(required=False,widget=forms.HiddenInput)
    revision=forms.IntegerField(required=False,widget=forms.HiddenInput)
    change_reason=forms.CharField(label="변경 사유",required=False,max_length=5000,widget=forms.Textarea(attrs={"rows":3}))
    class Meta:
        model=WorkRequest
        fields=['request_kind','title','description','department','coordinator','project','customer','solution','requested_start_date','requested_due_date','response_due_date','headcount','preferred_employee','location','resource_url']
        labels={'department':'지원받을 팀','requested_due_date':'희망 투입 종료일 · 업무 마감일','description':'요청 목적 · 수행 범위 · 완료 조건'}
        widgets={'description':forms.Textarea(attrs={'rows':5}),**{n:forms.DateInput(attrs={'type':'date'}) for n in ['requested_start_date','requested_due_date','response_due_date']}}
    def __init__(self,*args,user=None,**kw):
        super().__init__(*args,**kw);self.user=user
        self.initial.setdefault('submission',uuid.uuid4())
        self.initial.setdefault('revision',self.instance.revision)
        if self.instance.pk:self.fields['department'].disabled=True
        else:self.fields.pop('change_reason')
        self.fields['request_kind'].choices=[('cooperation','업무협조 요청'),('support','엔지니어 지원 요청')]
        self.fields['project'].queryset=visible_projects(user)
        active=Employee.objects.filter(is_active=True,user__is_active=True).select_related('department','user')
        self.fields['coordinator'].queryset=active
        self.fields['preferred_employee'].queryset=active
        self.fields['coordinator'].widget=TeamEmployeeSelect()
        self.fields['preferred_employee'].widget=TeamEmployeeSelect()
        self.fields['headcount'].required=False
        self.fields['request_kind'].required=False
        self.fields['coordinator'].help_text='팀장 공석 시 지정할 접수 대행자입니다. 선택한 팀의 직원만 지정할 수 있습니다.'
        self.fields['preferred_employee'].help_text='희망 인원이며 확정 배정이 아닙니다. 최종 담당자는 수신 팀에서 정합니다.'
        self.fields['resource_url'].help_text='회사 공유 자료 링크를 입력하세요. 해당 저장소의 열람 권한은 별도로 부여해야 합니다.'
        self.fields['requested_due_date'].help_text='배정 회신 기한과 실제 수행 마감일을 구분합니다.'
        self.initial.setdefault('request_kind','cooperation');self.initial.setdefault('requested_start_date',timezone.localdate())
    def clean(self):
        d=super().clean();d['request_kind']=d.get('request_kind') or 'cooperation';d['headcount']=d['headcount'] if d.get('headcount') is not None else 1
        team=d.get('department');coord=d.get('coordinator');pref=d.get('preferred_employee')
        for field,emp in [('coordinator',coord),('preferred_employee',pref)]:
            if emp and team and emp.department_id!=team.pk:self.add_error(field,'선택한 지원 팀 소속 직원을 지정하세요.')
        start,end,reply=d.get('requested_start_date'),d.get('requested_due_date'),d.get('response_due_date')
        if start and end and end<start:self.add_error('requested_due_date','희망 종료일은 시작일보다 빠를 수 없습니다.')
        if start and reply and reply>start:self.add_error('response_due_date','배정 회신 기한은 희망 투입 시작일 이내로 지정하세요.')
        if d['request_kind']=='support':
            for f in ['customer','solution','requested_start_date','response_due_date']:
                if not d.get(f):self.add_error(f,'엔지니어 지원 요청에 필요한 정보입니다.')
        project=d.get('project')
        if project and end and (end>project.due_date or (start and start<project.start_date)):
            self.add_error('project','희망 투입 기간은 프로젝트 기간 안에 지정하세요.')
        if self.instance.pk and not (d.get('change_reason') or '').strip():self.add_error('change_reason','변경 사유를 입력하세요.')
        return d


@login_required
def register(request):
    if not can_view_company(request.user) and not profile(request.user):raise PermissionDenied
    return render(request,'tasks/register.html')


@login_required
def intake_list(request):
    if not can_view_company(request.user) and not profile(request.user):raise PermissionDenied
    qs=visible_requests(request.user)
    box=request.GET.get('box','sent' if not manages_team(request.user) else 'received')
    if box not in ['sent','received','all']:box='sent'
    if box=='sent':qs=qs.filter(creator=request.user)
    elif box=='received':
        emp=profile(request.user)
        if not can_view_company(request.user):qs=qs.filter(Q(department_id=emp.department_id) if emp and manages_team(request.user) else Q(coordinator__user=request.user))
    state=request.GET.get('state','pending')
    if state=='assigned':qs=qs.filter(task__isnull=False)
    elif state=='closed':qs=qs.filter(Q(closed_at__isnull=False)|Q(stage='rejected'))
    elif state=='all':pass
    else:state='pending';qs=qs.filter(task__isnull=True,closed_at__isnull=True).exclude(stage='rejected')
    direct=visible_tasks(request.user).filter(creation_kind='request')
    if box=='sent':direct=direct.filter(creator=request.user)
    elif box=='received':direct=direct.exclude(creator=request.user)
    if state=='closed':direct=direct.filter(status='cancelled')
    elif state=='pending':direct=direct.none()
    elif state=='assigned':direct=direct.exclude(status__in=['cancelled','draft'])
    else:direct=direct.exclude(status='draft')
    return render(request,'tasks/intake_list.html',{'requests':qs[:150],'state':state,'box':box,'managing':manages_team(request.user),'direct_tasks':direct[:100]})


@login_required
@retry_busy
@transaction.atomic
def intake_new(request):
    if not can_view_company(request.user) and not profile(request.user):raise PermissionDenied
    form=RequestForm(request.POST if request.method=='POST' else None,user=request.user)
    if request.method=='POST' and form.is_valid():
        key=form.cleaned_data.get('submission')
        existing=WorkRequest.objects.filter(submission_key=key).first() if key else None
        if existing:
            if existing.creator_id!=request.user.pk:raise PermissionDenied
            return redirect('intake_detail',pk=existing.pk)
        obj=form.save(commit=False);obj.submission_key=key;emp=profile(request.user);obj.creator=request.user;obj.creator_name=emp.name if emp else request.user.get_username();obj.source_department=emp.department if emp else None;obj.save()
        event(obj,request.user,'요청 등록','지원 팀에 업무협조를 요청했습니다.')
        return redirect('intake_detail',pk=obj.pk)
    return render(request,'tasks/request_form.html',{'title':'업무협조 요청','form':form,'request_form':True})


@login_required
@retry_busy
@transaction.atomic
def intake_edit(request,pk):
    obj=get_object_or_404(visible_requests(request.user).select_for_update(),pk=pk)
    if obj.creator_id!=request.user.pk or obj.task_id or obj.closed_at or obj.stage=='rejected':raise PermissionDenied
    before={str(obj._meta.get_field(f).verbose_name):str(getattr(obj,f[:-3] if f.endswith('_id') else f)) for f in ['title','description','project_id','customer','solution','requested_start_date','requested_due_date','response_due_date','headcount','preferred_employee_id','location','resource_url']}
    form=RequestForm(request.POST if request.method=='POST' else None,user=request.user,instance=obj)
    if request.method=='POST' and form.is_valid():
        try:claim(obj,form.cleaned_data.get('revision') or 0)
        except ValidationError as exc:form.add_error(None,exc)
        else:
            if obj.stage=='needs_info':obj.stage='received'
            form.save();event(obj,request.user,'요청 내용 변경',form.cleaned_data['change_reason'],{'before':before})
            return redirect('intake_detail',pk=pk)
    return render(request,'tasks/request_form.html',{'form':form,'intake':obj,'request_form':True,'title':'요청 내용 수정 · 보완'})


@login_required
def intake_detail(request,pk):
    obj=get_object_or_404(visible_requests(request.user),pk=pk)
    manager=can_assign(request.user,obj)
    current_ids=set(obj.task.participants.values_list('pk',flat=True))|{obj.task.assignee_id} if obj.task_id else set()
    staffing=obj.staffing.filter(employee_id__in=current_ids).select_related('employee__department')
    can_open_task=bool(obj.task_id and visible_tasks(request.user).filter(pk=obj.task_id).exists())
    return render(request,'tasks/intake_detail.html',{'intake':obj,'can_assign':manager,'can_close':obj.creator_id==request.user.pk or manager,
        'can_open_task':can_open_task,'events':obj.events.all(),'staffing':staffing,
        'receiver_missing':not Employee.objects.filter(user_id__in=recipients(obj),department_id=obj.department_id).exists(),
        'can_coordinate':manager or obj.coordinator_id and obj.coordinator.user_id==request.user.pk,
        'can_confirm':obj.creator_id==request.user.pk and obj.task_id and obj.task.status=='done' and not obj.confirmed_at})


class RequestActionForm(forms.Form):
    revision=forms.IntegerField(widget=forms.HiddenInput,required=False,min_value=0)
    body=forms.CharField(label='내용 · 사유',max_length=10000,widget=forms.Textarea(attrs={'rows':5}))


def claim(obj,revision):
    if revision!=obj.revision:raise ValidationError('다른 변경이 반영되었습니다. 요청 화면에서 최신 내용을 확인하세요.')
    if not WorkRequest.objects.filter(pk=obj.pk,revision=obj.revision).update(revision=F('revision')+1):raise ValidationError('이미 처리된 요청입니다. 최신 내용을 확인하세요.')
    obj.revision+=1


@login_required
@retry_busy
def request_action(request,pk,action):
    titles={'receive':'접수 확인','negotiate':'일정 · 인력 협의','needs_info':'요청 내용 보완 요청','resubmit':'요청 내용 보완','reject':'지원 불가 회신','comment':'질문 · 의견','confirm':'결과 확인','followup':'추가 지원 · 보완 요청','cancel':'요청 취소'}
    if action not in titles:raise PermissionDenied
    obj=get_object_or_404(visible_requests(request.user),pk=pk)
    manager=can_assign(request.user,obj);owner=obj.creator_id==request.user.pk
    coordinator=obj.coordinator_id and obj.coordinator.user_id==request.user.pk
    if action in ['receive','negotiate','needs_info'] and not (manager or coordinator):raise PermissionDenied
    if action=='reject' and not manager:raise PermissionDenied
    if action in ['resubmit','confirm','followup'] and not owner:raise PermissionDenied
    if action=='cancel' and not (owner or manager):raise PermissionDenied
    data=request.POST if request.method=='POST' else None
    # Legacy close form uses reason; keep old open forms usable.
    if data is not None and 'reason' in data and 'body' not in data:data=data.copy();data['body']=data['reason'];data['revision']=obj.revision
    form=RequestActionForm(data,initial={'revision':obj.revision})
    if request.method=='POST' and form.is_valid():
        try:
            with transaction.atomic():
                obj=get_object_or_404(visible_requests(request.user).select_for_update(),pk=pk)
                if obj.closed_at or obj.stage=='rejected':raise ValidationError('종료된 요청입니다. 필요한 경우 새 요청을 등록하세요.')
                if action in ['receive','negotiate','needs_info','resubmit','reject','cancel'] and obj.task_id:raise ValidationError('배정된 요청은 수행 업무에서 변경·취소하고 사유를 남기세요.')
                if action=='resubmit' and obj.stage!='needs_info':raise ValidationError('보완 요청 상태에서 내용을 보완하세요.')
                if action in ['confirm','followup'] and (not obj.task_id or obj.task.status!='done' or obj.confirmed_at):raise ValidationError('수행 업무 완료 후 요청자가 결과를 확인할 수 있습니다.')
                claim(obj,form.cleaned_data.get('revision') or 0)
                if action in ['receive','negotiate','needs_info','reject','resubmit']:obj.stage={'receive':'received','negotiate':'negotiating','needs_info':'needs_info','reject':'rejected','resubmit':'received'}[action]
                if action=='confirm':obj.confirmed_at=timezone.now();obj.confirmation_note=form.cleaned_data['body']
                if action=='cancel':obj.closed_at=timezone.now();obj.close_reason=request.user.get_username()+': '+form.cleaned_data['body']
                obj.save()
                extra={}
                if action=='followup':
                    fields={n:getattr(obj,n) for n in ['request_kind','source_department','department','coordinator','project','customer','solution','headcount','preferred_employee','location','resource_url','creator','creator_name']}
                    fields.update(title=('추가 지원 · '+obj.title)[:160],description=form.cleaned_data['body'],requested_start_date=timezone.localdate(),requested_due_date=max(timezone.localdate(),obj.requested_due_date),parent_request=obj)
                    followup=WorkRequest.objects.create(**fields)
                    event(followup,request.user,'추가 지원 요청',form.cleaned_data['body'],{'parent_request':obj.pk});extra={'followup_request':followup.pk}
                event(obj,request.user,titles[action],form.cleaned_data['body'],extra)
        except ValidationError as exc:form.add_error(None,exc)
        else:return redirect('intake_detail',pk=pk)
    return render(request,'tasks/request_form.html',{'title':titles[action],'form':form,'intake':obj})


@login_required
def intake_close(request,pk):return request_action(request,pk,'cancel')


@login_required
@retry_busy
def intake_assign(request,pk):
    obj=get_object_or_404(visible_requests(request.user),pk=pk)
    if not can_assign(request.user,obj):raise PermissionDenied
    if obj.task_id:return redirect('task_detail',pk=obj.task_id)
    if obj.closed_at or obj.stage=='rejected':raise PermissionDenied
    # Retain the previous handler for records created before this version.
    if obj.request_kind=='legacy':
        from .operations import intake_assign as old_assign
        return old_assign(request,pk)
    from .collaboration import edit_task
    return edit_task(request,intake_override=obj)


class StaffingForm(forms.Form):
    employee=forms.IntegerField(widget=forms.HiddenInput)
    role=forms.CharField(label='담당 역할',max_length=120)
    start_date=forms.DateField(label='투입 시작일',widget=forms.DateInput(attrs={'type':'date'}))
    end_date=forms.DateField(label='투입 종료일',widget=forms.DateInput(attrs={'type':'date'}))
    def clean(self):
        d=super().clean()
        if d.get('start_date') and d.get('end_date') and d['end_date']<d['start_date']:self.add_error('end_date','종료일은 시작일보다 빠를 수 없습니다.')
        return d


@login_required
@retry_busy
def staffing_edit(request,pk):
    obj=get_object_or_404(visible_requests(request.user),pk=pk)
    if not can_assign(request.user,obj) or not obj.task_id:raise PermissionDenied
    if obj.task.status in ['done','cancelled','review']:raise PermissionDenied('진행 중인 업무의 투입 정보만 변경할 수 있습니다.')
    ids=set(obj.task.participants.values_list('pk',flat=True))|{obj.task.assignee_id}
    employees=list(Employee.objects.filter(pk__in=ids).order_by('pk'))
    saved={r.employee_id:r for r in obj.staffing.all()}
    initial=[{'employee':e.pk,'role':saved[e.pk].role if e.pk in saved else '수행 담당','start_date':saved[e.pk].start_date if e.pk in saved else obj.task.start_date,'end_date':saved[e.pk].end_date if e.pk in saved else obj.task.due_date} for e in employees]
    FormSet=forms.formset_factory(StaffingForm,extra=0,max_num=100,validate_max=True)
    fs=FormSet(request.POST if request.method=='POST' else None,initial=initial)
    control=RequestActionForm(request.POST if request.method=='POST' else None,initial={'revision':obj.revision})
    if request.method=='POST' and fs.is_valid() and control.is_valid():
        try:
            with transaction.atomic():
                obj=get_object_or_404(visible_requests(request.user).select_for_update(),pk=pk)
                current=set(obj.task.participants.values_list('pk',flat=True))|{obj.task.assignee_id}
                supplied=[f.cleaned_data['employee'] for f in fs]
                if set(supplied)!=current or len(supplied)!=len(current):raise ValidationError('수행자가 변경되었습니다. 화면을 다시 확인하세요.')
                if obj.task.status in ['done','cancelled','review']:raise ValidationError('지금은 투입 정보를 변경할 수 없습니다.')
                if any(f.cleaned_data['start_date']<obj.task.start_date or f.cleaned_data['end_date']>obj.task.due_date for f in fs):raise ValidationError('개인 투입 기간은 배정된 업무 기간 안으로 지정하세요.')
                claim(obj,control.cleaned_data.get('revision') or 0)
                before=[{'employee':r.employee_id,'role':r.role,'start':str(r.start_date),'end':str(r.end_date)} for r in obj.staffing.all()]
                for f in fs:
                    d=f.cleaned_data.copy();emp=d.pop('employee');RequestAllocation.objects.update_or_create(request=obj,employee_id=emp,defaults=d)
                obj.save(update_fields=['revision']);event(obj,request.user,'투입 정보 변경',control.cleaned_data['body'],{'previous':before,'staffing':[{k:str(v) for k,v in f.cleaned_data.items()} for f in fs]})
        except ValidationError as exc:control.add_error(None,exc)
        else:return redirect('intake_detail',pk=pk)
    return render(request,'tasks/request_staffing.html',{'intake':obj,'formset':fs,'rows':zip(employees,fs),'form':control})


@login_required
@require_POST
def read_notification(request,pk):
    n=get_object_or_404(RequestNotification,pk=pk,user=request.user,request__in=visible_requests(request.user));n.read_at=timezone.now();n.save(update_fields=['read_at']);return redirect('intake_detail',pk=n.request_id)
