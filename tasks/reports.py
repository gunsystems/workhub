"""Weekly/period reporting built from existing work and immutable submissions."""
from datetime import date,timedelta
from pathlib import Path
from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied,ValidationError
from django.db import transaction
from django.db.models import Q,F
from django.http import FileResponse,Http404
from django.shortcuts import get_object_or_404,render,redirect
from django.utils import timezone
from .models import PeriodReport,ReportSubmission,ReportReview,ReportFile,Task,Employee
from .permissions import profile,can_view_company,visible_tasks,visible_employees
from .collaboration import UserChoice,users


def visible_reports(user):
    qs=PeriodReport.objects.select_related('author__employee__department','reviewer','current_submission')
    if can_view_company(user): return qs.exclude(Q(state='draft') & ~Q(author=user))
    emp=profile(user)
    if not emp:return qs.none()
    return qs.filter(Q(author=user)|Q(reviewer=user)|Q(author__employee__department=emp.department)).exclude(Q(state='draft') & ~Q(author=user))


def selectable_sources(user):
    # Only already-submitted versions in the user's team/oversight scope.
    qs=ReportSubmission.objects.filter(report__in=visible_reports(user)).exclude(report__author=user)
    return qs.select_related('report__author').order_by('-created_at')


class ReportForm(forms.ModelForm):
    revision=forms.IntegerField(widget=forms.HiddenInput,initial=0)
    reviewer=UserChoice(label='보고받을 사람',queryset=get_user_model().objects.none())
    tasks=forms.ModelMultipleChoiceField(label='관련 업무 · 기존 기록 연결',queryset=Task.objects.none(),required=False,widget=forms.CheckboxSelectMultiple)
    sources=forms.ModelMultipleChoiceField(label='취합할 직원 보고 · 제출 버전 선택',queryset=ReportSubmission.objects.none(),required=False,widget=forms.CheckboxSelectMultiple)
    attachment=forms.FileField(label='첨부 (선택 · 파일당 10MB)',required=False)
    class Meta:
        model=PeriodReport
        fields=['period_start','period_end','summary','reviewer']
        widgets={'period_start':forms.DateInput(attrs={'type':'date'}),'period_end':forms.DateInput(attrs={'type':'date'}),'summary':forms.Textarea(attrs={'rows':6})}
    def __init__(self,*args,user,**kwargs):
        super().__init__(*args,**kwargs);self.user=user
        self.fields['reviewer'].queryset=users().exclude(pk=user.pk)
        self.fields['tasks'].queryset=visible_tasks(user).exclude(status='draft')
        emp=profile(user)
        if can_view_company(user) or (emp and (emp.position=='leader' or emp.role=='manager')):
            self.fields['sources'].queryset=selectable_sources(user)
        self.fields['sources'].label_from_instance=lambda s:f'{s.report.author.get_username()} · {s.report.period_start}~{s.report.period_end} · 제출 v{s.version}'
        if self.instance.pk:
            self.initial['revision']=self.instance.revision
            self.initial['sources']=list(self.instance.source_submissions.values_list('pk',flat=True))
            self.initial['tasks']=list(self.instance.selected_tasks.values_list('pk',flat=True))
    def clean(self):
        data=super().clean()
        if data.get('period_start') and data.get('period_end') and (data['period_end']<data['period_start'] or (data['period_end']-data['period_start']).days>366):self.add_error('period_end','보고 기간은 시작일 이후 1년 이내로 지정하세요.')
        if self.instance.pk and data.get('revision')!=self.instance.revision:self.add_error(None,'다른 변경이 반영되었습니다. 다시 열어 주세요.')
        f=data.get('attachment')
        if f and (f.size>10*1024*1024 or Path(f.name).suffix.lower() not in ['.pdf','.docx','.xlsx','.pptx','.txt','.csv','.png','.jpg','.jpeg']):
            self.add_error('attachment','10MB 이하 PDF·Office 문서·텍스트·이미지 파일을 사용하세요.')
        if f and self.instance.pk and self.instance.files.count()>=10:self.add_error('attachment','보고서별 첨부는 최대 10개입니다.')
        return data


@login_required
def report_list(request):
    emp=profile(request.user)
    if not emp and not request.user.is_superuser:raise PermissionDenied
    try:start=date.fromisoformat(request.GET.get('week',''))
    except ValueError:
        today=timezone.localdate();start=today-timedelta(days=today.weekday())
    end=start+timedelta(days=6)
    qs=visible_reports(request.user).filter(period_start__lte=end,period_end__gte=start)
    managing=can_view_company(request.user) or (emp and (emp.position=='leader' or emp.role=='manager'))
    missing=[]
    if managing:
        submitted_ids=qs.filter(state__in=['submitted','approved','returned']).values_list('author_id',flat=True)
        missing=visible_employees(request.user).filter(is_active=True,user__is_active=True).exclude(user__isnull=True).exclude(user_id__in=submitted_ids)
    return render(request,'tasks/report_list.html',{'reports':qs.order_by('-period_start','-pk')[:100],'start':start,'end':end,'missing':missing,'managing':managing})


@login_required
@transaction.atomic
def report_edit(request,pk=None):
    emp=profile(request.user)
    if not emp and not request.user.is_superuser:raise PermissionDenied
    obj=get_object_or_404(PeriodReport.objects.select_for_update(),pk=pk,author=request.user) if pk else None
    if obj and obj.state in ['submitted','approved']:raise PermissionDenied('제출/승인된 보고는 보완 요청 이후에 수정할 수 있습니다.')
    today=timezone.localdate();start=today-timedelta(days=today.weekday())
    initial={'period_start':start,'period_end':start+timedelta(days=6),'reviewer':emp.supervisor.user_id if emp and emp.supervisor else None}
    form=ReportForm(request.POST if request.method=='POST' else None,request.FILES or None,instance=obj,user=request.user,initial=initial)
    if request.method=='POST' and form.is_valid():
        obj=form.save(commit=False);obj.author=request.user;obj.revision+=1;obj.save()
        obj.source_submissions.set(form.cleaned_data['sources'])
        obj.selected_tasks.set(form.cleaned_data['tasks'])
        f=form.cleaned_data.get('attachment')
        if f:ReportFile.objects.create(report=obj,file=f,name=Path(f.name).name[:200])
        if request.POST.get('action')=='submit':
            tasks=list(form.cleaned_data['tasks'])
            snapshot={'summary':obj.summary,'period_start':str(obj.period_start),'period_end':str(obj.period_end),
                'author':request.user.get_username(),'reviewer':obj.reviewer.get_username(),
                'task_ids':[t.pk for t in tasks],
                'tasks':[{'id':t.pk,'title':t.title,'status':t.get_status_display(),'progress':t.progress,'due_date':str(t.due_date),'next_action':t.next_action,
                          'reports':list(t.events.filter(kind__in=['progress','submitted']).order_by('-created_at').values('body','kind')[:5])} for t in tasks],
                'source_submission_ids':list(obj.source_submissions.values_list('pk',flat=True)),
                'files':list(obj.files.values('pk','name'))}
            submission=ReportSubmission.objects.create(report=obj,version=obj.revision,snapshot=snapshot)
            obj.current_submission=submission;obj.state='submitted';obj.submitted_at=timezone.now();obj.save(update_fields=['current_submission','state','submitted_at'])
        return redirect('period_report_detail',pk=obj.pk)
    return render(request,'tasks/report_form.html',{'form':form,'report':obj})


@login_required
@transaction.atomic
def report_detail(request,pk):
    obj=get_object_or_404(visible_reports(request.user).select_for_update(),pk=pk)
    review_error=''
    if request.method=='POST':
        if obj.reviewer_id!=request.user.pk or obj.author_id==request.user.pk:raise PermissionDenied
        if obj.state!='submitted' or request.POST.get('revision')!=str(obj.revision):review_error='다른 변경이 반영되었습니다. 현재 제출본을 다시 확인하세요.'
        elif request.POST.get('decision') not in ['approved','returned'] or not request.POST.get('body','').strip():review_error='검토 결과와 사유를 입력하세요.'
        else:
            obj.state=request.POST['decision'];obj.revision+=1;obj.save(update_fields=['state','revision'])
            ReportReview.objects.create(submission=obj.current_submission,author=request.user,decision=obj.state,body=request.POST['body'][:10000])
            return redirect('period_report_detail',pk=pk)
    sources=ReportSubmission.objects.filter(pk__in=obj.current_submission.snapshot.get('source_submission_ids',[]) if obj.current_submission else obj.source_submissions.values_list('pk',flat=True),report__in=visible_reports(request.user))
    return render(request,'tasks/report_detail.html',{'report':obj,'submission':obj.current_submission,'sources':sources,'can_review':obj.reviewer_id==request.user.pk and obj.state=='submitted','review_error':review_error})


@login_required
def report_file(request,pk):
    obj=get_object_or_404(ReportFile.objects.select_related('report'),pk=pk,report__in=visible_reports(request.user))
    try:response=FileResponse(obj.file.open('rb'),as_attachment=True,filename=obj.name,content_type='application/octet-stream')
    except FileNotFoundError:raise Http404
    response['Cache-Control']='private, no-store';response['X-Content-Type-Options']='nosniff'
    return response
