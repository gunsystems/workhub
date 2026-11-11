"""Human assessments supplement facts; activity counts do not determine pay."""
import calendar
from datetime import date
from django import forms
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied,ValidationError
from django.db import transaction,OperationalError
from django.shortcuts import get_object_or_404,redirect,render
from django.utils import timezone
from .models import StaffAssessment,AssessmentRevision,Employee,Task
from .permissions import profile,visible_tasks

CRITERIA=[('quality','결과물 품질·목표 달성'),('contribution','실제 기여도·책임감'),('ability','업무수행 능력·문제 해결'),('attitude','업무 자세·주도성·노력'),('collaboration','협업·의사소통')]
LEVELS=[('','평가 제외 · 관찰 근거 부족'),('1','1 · 기대 수준에 크게 미달'),('2','2 · 일부 개선 필요'),('3','3 · 기대 수준 충족'),('4','4 · 기대 수준 초과'),('5','5 · 탁월한 기여')]


def facts(obj,user):
    from django.db.models import Q
    from .models import TaskEvent
    tasks=visible_tasks(user).filter(Q(assignee=obj.employee)|Q(participants=obj.employee)).exclude(status__in=['draft','cancelled']).distinct()
    tasks=tasks.filter(pk=obj.task_id) if obj.task_id else tasks.filter(due_date__range=(obj.period_start,obj.period_end))
    rows=[]
    for task in tasks[:100]:
        assignment=next((a for a in task.assignments.all() if a.employee_id==obj.employee_id),None)
        accepted=assignment.accepted_at if assignment else task.accepted_at
        assigned=assignment.assigned_at if assignment else task.assigned_at
        completed=assignment.completed_at if assignment else task.completed_at
        reports=TaskEvent.objects.filter(task=task,author_id=obj.employee.user_id,kind='progress',created_at__date__range=(obj.period_start,obj.period_end)).filter(Q(snapshot__action='progress')|~Q(snapshot__has_key='action')).count()
        wait=max(0,int((accepted-assigned).total_seconds()/60)) if accepted and assigned else None
        rows.append({'task':task,'accepted':accepted,'wait':wait,'completed':completed,'reports':reports})
    return rows


def executive(user):
    employee=profile(user)
    return bool(employee and employee.position in ['head','executive'])


def require_executive(user):
    if not executive(user):raise PermissionDenied('지정 임원만 평가를 열람·작성할 수 있습니다.')


class AssessmentForm(forms.ModelForm):
    reason=forms.CharField(label='저장·수정 사유',max_length=300)
    expected_revision=forms.IntegerField(widget=forms.HiddenInput,initial=0)
    class Meta:
        model=StaffAssessment
        fields=['employee','task','period_start','period_end','evidence','context']
        labels={'employee':'평가 대상','task':'관련 업무 · 선택'}
        widgets={'period_start':forms.DateInput(attrs={'type':'date'},format='%Y-%m-%d'),'period_end':forms.DateInput(attrs={'type':'date'},format='%Y-%m-%d'),'evidence':forms.Textarea(attrs={'rows':5}),'context':forms.Textarea(attrs={'rows':3})}
    def __init__(self,*args,user,**kwargs):
        super().__init__(*args,**kwargs)
        self.fields['employee'].queryset=Employee.objects.filter(is_active=True,user__is_active=True).exclude(user=user).select_related('department')
        self.fields['task'].queryset=visible_tasks(user).exclude(status='draft')
        for key,label in CRITERIA:
            self.fields[key]=forms.TypedChoiceField(label=label,choices=LEVELS,coerce=int,empty_value=None,required=False,initial=self.instance.scores.get(key))
        self.fields['expected_revision'].initial=self.instance.revision
    def clean(self):
        d=super().clean()
        if d.get('period_start') and d.get('period_end') and d['period_end']<d['period_start']:self.add_error('period_end','시작일 이후로 지정하세요.')
        if not any(d.get(k) for k,_ in CRITERIA):self.add_error(None,'관찰 근거가 있는 항목을 하나 이상 평가하세요.')
        task=d.get('task');employee=d.get('employee')
        if task and employee and not(task.assignee_id==employee.pk or task.participants.filter(pk=employee.pk).exists()):self.add_error('employee','해당 업무의 실제 수행자를 선택하세요.')
        return d


@transaction.atomic
def save_assessment(form,user,final=False):
    require_executive(user)
    obj=form.instance
    if obj.pk:
        current=StaffAssessment.objects.select_for_update().get(pk=obj.pk)
        if current.author_id!=user.pk:raise PermissionDenied('다른 임원의 평가는 수정할 수 없습니다.')
        if current.revision!=form.cleaned_data['expected_revision']:raise ValidationError('다른 화면에서 수정되었습니다. 새로고침 후 확인하세요.')
        if current.state=='final' and not final:raise ValidationError('확정된 평가는 수정 사유를 남겨 다시 확정해야 합니다.')
    else:obj.author=user
    if obj.employee.user_id==user.pk:raise PermissionDenied('본인 평가는 작성할 수 없습니다.')
    obj.scores={k:form.cleaned_data[k] for k,_ in CRITERIA if form.cleaned_data.get(k) is not None}
    obj.state='final' if final else 'draft';obj.revision+=1;obj.updated_at=timezone.now();obj.save()
    AssessmentRevision.objects.create(assessment=obj,actor=user,revision=obj.revision,reason=form.cleaned_data['reason'],snapshot={
        'scores':obj.scores,'evidence':obj.evidence,'context':obj.context,'state':obj.state,
        'employee':obj.employee_id,'task':obj.task_id,'period_start':obj.period_start.isoformat(),'period_end':obj.period_end.isoformat()})
    return obj


@login_required
def edit(request,pk=None):
    require_executive(request.user)
    obj=get_object_or_404(StaffAssessment,pk=pk) if pk else StaffAssessment(author=request.user,period_start=timezone.localdate().replace(day=1),period_end=timezone.localdate())
    if obj.pk and obj.author_id!=request.user.pk:raise PermissionDenied
    form=AssessmentForm(request.POST if request.method=='POST' else None,instance=obj,user=request.user)
    if request.method=='POST' and form.is_valid():
        try:obj=save_assessment(form,request.user,final=request.POST.get('action')=='final')
        except ValidationError as exc:form.add_error(None,exc.messages[0])
        except OperationalError:form.add_error(None,'다른 처리가 진행 중입니다. 새로고침 후 평가 상태를 확인하세요.')
        else:return redirect('assessment_detail',pk=obj.pk)
    return render(request,'tasks/assessment_form.html',{'form':form,'assessment':obj})


@login_required
def detail(request,pk):
    require_executive(request.user)
    obj=get_object_or_404(StaffAssessment.objects.select_related('employee__department','author','task'),pk=pk)
    if obj.state!='final' and obj.author_id!=request.user.pk:raise PermissionDenied
    scores=[{'label':label,'value':obj.scores.get(key)} for key,label in CRITERIA]
    return render(request,'tasks/assessment_detail.html',{'assessment':obj,'scores':scores,'facts':facts(obj,request.user),'history':obj.history.select_related('actor').order_by('-revision')})


@login_required
def overview(request):
    require_executive(request.user)
    today=timezone.localdate()
    class Filter(forms.Form):
        year=forms.IntegerField(label='연도',min_value=2000,max_value=2100,initial=today.year)
        period=forms.ChoiceField(label='집계 범위',choices=[('month','월별'),('quarter','분기별'),('year','연간')],initial='month')
        month=forms.IntegerField(label='기준 월',min_value=1,max_value=12,initial=today.month)
    form=Filter(request.GET or None)
    d=form.cleaned_data if form.is_bound and form.is_valid() else {'year':today.year,'period':'month','month':today.month}
    year=d['year'];month=d['month'];kind=d['period']
    first=1 if kind=='year' else ((month-1)//3*3+1 if kind=='quarter' else month)
    last=12 if kind=='year' else first+2 if kind=='quarter' else first
    start=date(year,first,1);end=date(year,last,calendar.monthrange(year,last)[1])
    # Only fully contained periods; no double counting a cross-year assessment.
    qs=StaffAssessment.objects.filter(state='final',period_start__gte=start,period_end__lte=end).select_related('employee__department','author','task')
    summaries=[]
    for employee in Employee.objects.filter(pk__in=qs.values('employee_id')).select_related('department'):
        for task_based,label in [(True,'업무별 평가'),(False,'기간 종합 평가')]:
            records=[x for x in qs if x.employee_id==employee.pk and bool(x.task_id)==task_based]
            if not records:continue
            averages=[]
            for key,criterion in CRITERIA:
                vals=[x.scores[key] for x in records if key in x.scores]
                averages.append({'label':criterion,'value':round(sum(vals)/len(vals),2) if vals else None,'count':len(vals)})
            summaries.append({'employee':employee,'group':label,'count':len(records),'averages':averages})
    own_drafts=StaffAssessment.objects.filter(author=request.user,state='draft').select_related('employee__department')
    return render(request,'tasks/assessment_list.html',{'form':form,'start':start,'end':end,'summaries':summaries,'assessments':qs,'drafts':own_drafts})
