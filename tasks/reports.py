"""Weekly/period reporting built from existing work and immutable submissions."""
from datetime import date,timedelta
from pathlib import Path
from django import forms
from django.contrib import messages
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
from .drive_storage import RepositoryError
from .weekly_reporting import calendar_draft,week_start,responsible_reviewer,submission_board


def visible_reports(user):
    qs=PeriodReport.objects.select_related('author__employee__department','reviewer','current_submission')
    if can_view_company(user): return qs.exclude(Q(state='draft') & ~Q(author=user))
    emp=profile(user)
    if not emp:return qs.none()
    scope=Q(author=user)|Q(reviewer=user)
    if emp.position=='leader' or emp.role=='manager':scope|=Q(author__employee__department=emp.department)
    return qs.filter(scope).exclude(Q(state='draft') & ~Q(author=user))


def selectable_sources(user):
    # Only already-submitted versions in the user's team/oversight scope.
    qs=ReportSubmission.objects.filter(report__in=visible_reports(user)).exclude(report__author=user)
    return qs.select_related('report__author').order_by('-created_at')


class ReportForm(forms.ModelForm):
    revision=forms.IntegerField(widget=forms.HiddenInput,initial=0)
    reviewer=UserChoice(label='보고받을 사람',queryset=get_user_model().objects.none())
    tasks=forms.ModelMultipleChoiceField(label='참조할 업무',queryset=Task.objects.none(),required=False,widget=forms.CheckboxSelectMultiple)
    sources=forms.ModelMultipleChoiceField(label='취합할 직원 보고 · 제출 버전 선택',queryset=ReportSubmission.objects.none(),required=False,widget=forms.CheckboxSelectMultiple)
    attachment=forms.FileField(widget=forms.ClearableFileInput(attrs={'data-upload-limit':'10485760','accept':'.pdf,.docx,.xlsx,.pptx,.txt,.csv,.png,.jpg,.jpeg'}),label='첨부 (선택 · 파일당 10MB)',required=False)
    class Meta:
        model=PeriodReport
        fields=['report_type','period_start','period_end','summary','next_week_plan','report_issues','reviewer']
        widgets={'period_start':forms.DateInput(attrs={'type':'date'}),'period_end':forms.DateInput(attrs={'type':'date'}),'summary':forms.Textarea(attrs={'rows':8}),'next_week_plan':forms.Textarea(attrs={'rows':6}),'report_issues':forms.Textarea(attrs={'rows':3})}
    def __init__(self,*args,user,**kwargs):
        # Preserve compatibility with report forms opened before this field existed.
        if args and args[0] is not None and 'report_type' not in args[0]:
            data=args[0].copy();data['report_type']=getattr(kwargs.get('instance'),'report_type','period');args=(data,*args[1:])
        elif kwargs.get('data') is not None and 'report_type' not in kwargs['data']:
            kwargs['data']=kwargs['data'].copy();kwargs['data']['report_type']=getattr(kwargs.get('instance'),'report_type','period')
        super().__init__(*args,**kwargs);self.user=user
        kind=self.data.get('report_type') if self.is_bound else self.initial.get('report_type',self.instance.report_type)
        if kind=='weekly':self.fields['summary'].label='이번 주 진행 업무'
        self.fields['tasks'].help_text='보고 내용의 근거가 되는 원본 업무입니다. 캘린더에서 취합한 업무는 자동으로 선택됩니다.'
        self.fields['next_week_plan'].help_text='다음 주 월요일~일요일의 예정 업무를 정리하세요.'
        self.fields['report_type'].help_text='보고 목적에 맞는 구분을 선택하세요. 개별 보고는 특정 업무·사안에 대한 보고입니다.'
        self.fields['reviewer'].queryset=users().exclude(pk=user.pk)
        self.fields['tasks'].queryset=visible_tasks(user).exclude(status='draft')
        emp=profile(user)
        if can_view_company(user) or (emp and (emp.position=='leader' or emp.role=='manager')):
            self.fields['sources'].queryset=selectable_sources(user)
        def source_label(submission):
            author=submission.report.author
            name=(getattr(getattr(author,'employee',None),'name',None) or author.get_username()) if author else '삭제된 계정'
            data=submission.snapshot
            return f"{name} · {data.get('report_type_label','기간 보고')} · {data.get('period_start',submission.report.period_start)}~{data.get('period_end',submission.report.period_end)} · 제출 v{submission.version}"
        self.fields['sources'].label_from_instance=source_label
        if self.instance.pk:
            self.initial['revision']=self.instance.revision
            self.initial['sources']=list(self.instance.source_submissions.values_list('pk',flat=True))
            self.initial['tasks']=list(self.instance.selected_tasks.values_list('pk',flat=True))
    def clean(self):
        data=super().clean()
        if data.get('report_type')=='daily' and data.get('period_start') and data.get('period_end') and data['period_start']!=data['period_end']:
            self.add_error('period_end','일일 보고는 시작일과 종료일을 같은 날짜로 선택하세요.')
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
    start=week_start(start)
    end=start+timedelta(days=6)
    qs=visible_reports(request.user).filter(period_start__lte=end,period_end__gte=start)
    report_type=request.GET.get('report_type','')
    if report_type in dict(PeriodReport.REPORT_TYPES):qs=qs.filter(report_type=report_type)
    else:report_type=''
    managing=can_view_company(request.user) or (emp and (emp.position=='leader' or emp.role=='manager'))
    missing=[]
    if managing:
        submitted_ids=qs.filter(state__in=['submitted','approved','returned']).values_list('author_id',flat=True)
        missing=visible_employees(request.user).filter(is_active=True,user__is_active=True).exclude(user__isnull=True).exclude(user_id__in=submitted_ids)
    board=submission_board(request.user,start)
    return render(request,'tasks/report_list.html',{'reports':qs.order_by('-period_start','-pk')[:100],'start':start,'end':end,'missing':missing,'managing':managing,'report_type':report_type,'report_types':PeriodReport.REPORT_TYPES,'weekly_board':board,'submitted_count':sum(bool(row['report']) for row in board)})


@login_required
@transaction.atomic
def report_edit(request,pk=None,calendar_seed=False):
    emp=profile(request.user)
    if not emp and not request.user.is_superuser:raise PermissionDenied
    obj=get_object_or_404(PeriodReport.objects.select_for_update(),pk=pk,author=request.user) if pk else None
    if obj and obj.state in ['submitted','approved']:raise PermissionDenied('제출/승인된 보고는 보완 요청 이후에 수정할 수 있습니다.')
    today=timezone.localdate();start=today-timedelta(days=today.weekday())
    initial={} if obj else {'report_type':'weekly','period_start':start,'period_end':start+timedelta(days=6),'reviewer':emp.supervisor.user_id if emp and emp.supervisor else None}
    seed=None
    if calendar_seed and not obj and request.method=='GET':
        try:start=week_start(date.fromisoformat(request.GET.get('week','')))
        except ValueError:pass
        seed=calendar_draft(request.user,start)
        initial.update(report_type='weekly',period_start=start,period_end=start+timedelta(days=6),summary=seed['current'],next_week_plan=seed['next'],tasks=seed['task_ids'],reviewer=responsible_reviewer(emp))
    form=ReportForm(request.POST if request.method=='POST' else None,request.FILES or None,instance=obj,user=request.user,initial=initial)
    if request.method=='POST' and form.is_valid():
        obj=form.save(commit=False);obj.author=request.user;obj.revision+=1
        if calendar_seed:obj.calendar_based=True
        obj.save()
        obj.source_submissions.set(form.cleaned_data['sources'])
        obj.selected_tasks.set(form.cleaned_data['tasks'])
        f=form.cleaned_data.get('attachment')
        if f:
            try:
                with transaction.atomic():ReportFile.objects.create(report=obj,file=f,name=Path(f.name).name[:200])
            except RepositoryError as exc:
                form.add_error('attachment',str(exc))
                response=render(request,'tasks/report_form.html',{'form':form,'report':obj,'report_today':today,'calendar_seed':calendar_seed or bool(obj and obj.calendar_based),'seed':seed})
                transaction.set_rollback(True)
                return response
        if request.POST.get('action')=='submit':
            tasks=list(form.cleaned_data['tasks'])
            snapshot={'report_issues':obj.report_issues,'next_week_plan':obj.next_week_plan,'calendar_based':obj.calendar_based,'report_type':obj.report_type,'report_type_label':obj.get_report_type_display(),'summary':obj.summary,'period_start':str(obj.period_start),'period_end':str(obj.period_end),
                'author':request.user.get_username(),'reviewer':obj.reviewer.get_username(),
                'task_ids':[t.pk for t in tasks],
                'tasks':[{'id':t.pk,'title':t.title,'status':t.get_status_display(),'progress':t.progress,'due_date':str(t.due_date),'next_action':t.next_action,
                          'reports':list(t.events.filter(kind__in=['progress','submitted']).order_by('-created_at').values('body','kind')[:5])} for t in tasks],
                'source_submission_ids':list(obj.source_submissions.values_list('pk',flat=True)),
                'files':list(obj.files.values('pk','name'))}
            submission=ReportSubmission.objects.create(report=obj,version=obj.revision,snapshot=snapshot)
            obj.current_submission=submission;obj.state='submitted';obj.submitted_at=timezone.now();obj.save(update_fields=['current_submission','state','submitted_at'])
        if f:messages.success(request,'보고서 첨부파일 업로드가 완료되었습니다.',extra_tags='upload-success')
        return redirect('period_report_detail',pk=obj.pk)
    return render(request,'tasks/report_form.html',{'form':form,'report':obj,'report_today':today,'calendar_seed':calendar_seed or bool(obj and obj.calendar_based),'seed':seed})


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
    except RepositoryError:return render(request,'tasks/document_error.html',status=503)
    response['Cache-Control']='private, no-store';response['X-Content-Type-Options']='nosniff'
    return response
