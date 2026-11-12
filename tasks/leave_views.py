import uuid
from functools import wraps
from datetime import date
from django import forms
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.shortcuts import render,redirect,get_object_or_404
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods
from accounts.lifecycle import system_admin
from .models import Employee,StaffEmployment,LeaveYear,LeaveHoliday,LeaveEntry,LeaveRequest,HolidaySync,LeaveCalendarReview
from .permissions import profile,visible_employees
from . import leave

def preview_only(view):
    @wraps(view)
    def guarded(request,*args,**kwargs):
        if not request.user.is_active or not request.user.is_superuser:raise PermissionDenied
        return view(request,*args,**kwargs)
    return guarded

class ApplicationForm(forms.Form):
    kind=forms.ChoiceField(label='휴가 종류',choices=LeaveRequest._meta.get_field('kind').choices)
    start=forms.DateField(label='시작일',widget=forms.DateInput(attrs={'type':'date'}))
    end=forms.DateField(label='종료일',widget=forms.DateInput(attrs={'type':'date'}))
    reason=forms.CharField(label='신청 사유',max_length=1000,widget=forms.Textarea(attrs={'rows':3}))

class EmploymentForm(forms.ModelForm):
    class Meta:
        model=StaffEmployment
        fields=['employee','joined_on','ended_on','staff_number']
        labels={'employee':'직원'}
        widgets={'joined_on':forms.DateInput(attrs={'type':'date'}),'ended_on':forms.DateInput(attrs={'type':'date'})}
    def clean(self):
        data=super().clean()
        if data.get('ended_on') and data.get('joined_on') and data['ended_on']<data['joined_on']:raise ValidationError('퇴사일은 입사일 이후여야 합니다.')
        return data

class GrantForm(forms.Form):
    employee=forms.ModelChoiceField(label='직원',queryset=Employee.objects.none())
    year=forms.IntegerField(label='연도',min_value=2020,max_value=2100)
    amount=forms.DecimalField(label='부여·조정 일수 (차감 조정은 음수)',max_digits=7,decimal_places=2,min_value=-366,max_value=366)
    reason=forms.CharField(label='부여·조정 사유',max_length=500)
    key=forms.UUIDField(widget=forms.HiddenInput)
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs);self.fields['employee'].queryset=Employee.objects.filter(is_active=True)
    def clean_amount(self):
        value=self.cleaned_data['amount']
        if value==0 or value%1 not in (0,0.5,-0.5):raise ValidationError('0이 아닌 0.5일 단위로 입력하세요.')
        return value

class YearForm(forms.ModelForm):
    class Meta:model=LeaveYear;fields=['year','calendar_confirmed','note']
    def clean_year(self):
        value=self.cleaned_data['year']
        if not 2020<=value<=2100:raise ValidationError('2020~2100년을 입력하세요.')
        return value
    def clean(self):
        data=super().clean()
        from django.conf import settings
        from datetime import timedelta
        if data.get('calendar_confirmed') and getattr(settings,'WORKHUB_HOLIDAY_SYNC_ENABLED',True) and not HolidaySync.objects.filter(year=data.get('year'),last_success__gte=timezone.now()-timedelta(hours=48)).exists():
            raise ValidationError('공휴일 자동 연결을 먼저 완료해 주세요. 공식 자료 확인 후 운영 기준을 저장할 수 있습니다.')
        return data

class HolidayForm(forms.ModelForm):
    class Meta:
        model=LeaveHoliday;fields=['date','name'];widgets={'date':forms.DateInput(attrs={'type':'date'})}

def year_value(request):
    try:return min(2100,max(2020,int(request.GET.get('year',timezone.localdate().year))))
    except (ValueError,TypeError):return timezone.localdate().year

@login_required
@never_cache
@preview_only
@require_http_methods(['GET'])
def preparation_view(request):
    year=year_value(request) if 'year' in request.GET else timezone.localdate().year+1
    from .holidays import calendar_context
    return render(request,'tasks/leave_preparation.html',{**leave.preparation(year),**calendar_context(year)})

@login_required
@never_cache
@preview_only
@require_http_methods(['GET'])
def overview(request):
    year=year_value(request);e=profile(request.user)
    qs=leave.visible(request.user).filter(start__year=year)
    state=request.GET.get('state','all')
    if state in dict(LeaveRequest._meta.get_field('state').choices):qs=qs.filter(state=state)
    staff=visible_employees(request.user).filter(is_active=True).select_related('department')
    rows=[{'employee':person,'balance':leave.balance(person,year),'reserved':leave.reserved(person,year)} for person in staff]
    pending=[item for item in qs if (leave.current(item) and e and leave.current(item).approver_id==e.pk)]
    return render(request,'tasks/leave_list.html',{'items':qs,'rows':rows,'year':year,'state':state,'states':LeaveRequest._meta.get_field('state').choices,'pending_count':len(pending),'can_admin':system_admin(request.user),'can_apply':bool(e),'own_balance':leave.balance(e,year) if e else None,'own_reserved':leave.reserved(e,year) if e else None})

@login_required
@never_cache
@preview_only
@require_http_methods(['GET','POST'])
def new(request):
    if not profile(request.user):raise PermissionDenied
    form=ApplicationForm(request.POST or None,initial={'start':timezone.localdate(),'end':timezone.localdate()})
    if request.method=='POST' and form.is_valid():
        try:item=leave.submit(request.user,**form.cleaned_data)
        except ValidationError as exc:form.add_error(None,exc)
        else:return redirect('leave_detail',pk=item.pk)
    return render(request,'tasks/leave_form.html',{'form':form,'title':'휴가 신청'})

@login_required
@never_cache
@preview_only
@require_http_methods(['GET','POST'])
def detail(request,pk):
    item=get_object_or_404(leave.visible(request.user),pk=pk)
    error=''
    if request.method=='POST':
        try:
            try:revision=int(request.POST.get('revision',''))
            except ValueError:raise ValidationError('화면을 새로고침한 후 다시 처리하세요.')
            leave.change(pk,request.user,request.POST.get('action'),request.POST.get('body',''),revision)
        except ValidationError as exc:error=' / '.join(exc.messages)
        else:return redirect('leave_detail',pk=pk)
        item.refresh_from_db()
    e=profile(request.user);step=leave.current(item)
    can_decide=bool(step and e and step.approver_id==e.pk)
    owner=bool(e and item.employee_id==e.pk)
    bal=leave.balance(item.employee,item.start.year)
    return render(request,'tasks/leave_detail.html',{'item':item,'steps':item.steps.all(),'events':item.events.all(),'can_decide':can_decide,'owner':owner,'balance':bal,'projected':bal-item.days if item.state=='pending' else bal,'error':error,'current_step':step})

@login_required
@never_cache
@preview_only
@require_http_methods(['GET','POST'])
def settings_view(request):
    if not system_admin(request.user):raise PermissionDenied
    year=year_value(request)
    selected=request.GET.get('employee','')
    employment=EmploymentForm(instance=StaffEmployment.objects.filter(employee_id=selected).first() if selected.isdigit() else None,initial={'employee':selected} if selected.isdigit() else {})
    grant=GrantForm(initial={'year':year,'key':uuid.uuid4()});year_form=YearForm(instance=LeaveYear.objects.filter(year=year).first(),initial={'year':year});holiday=HolidayForm()
    error='';success=''
    if request.method=='POST':
        action=request.POST.get('action')
        if action=='sync_holidays':
            from .holidays import queue_year
            queue_year(year)
            return redirect(request.path+'?year='+str(year)+'&sync=queued')
        if action=='review_holidays':
            from .holidays import reconcile
            try:
                reconcile(int(request.POST.get('request_id','')),request.user,int(request.POST.get('revision','')))
            except (ValueError,ValidationError,LeaveRequest.DoesNotExist,LeaveCalendarReview.DoesNotExist):
                return render(request,'tasks/leave_review_error.html',{'year':year},status=409)
            return redirect(request.path+'?year='+str(year)+'&saved=1')
        if action=='employment':
            employee_id=request.POST.get('employee')
            obj=StaffEmployment.objects.filter(employee_id=employee_id).first() if str(employee_id).isdigit() else None
            employment=EmploymentForm(request.POST,instance=obj)
            form=employment
        elif action=='grant':form=grant=GrantForm(request.POST)
        elif action=='year':
            y=request.POST.get('year');obj=LeaveYear.objects.filter(year=y).first() if str(y).isdigit() else None
            form=year_form=YearForm(request.POST,instance=obj)
        elif action=='holiday':form=holiday=HolidayForm(request.POST)
        else:raise PermissionDenied
        if form.is_valid():
            with transaction.atomic():
                if action=='grant':
                    data=form.cleaned_data
                    entry,created=LeaveEntry.objects.get_or_create(key='grant:'+str(data['key']),defaults={k:data[k] for k in ['employee','year','amount','reason']}|{'actor':request.user})
                    if not created and any(getattr(entry,k)!=data[k] for k in ['employee','year','amount','reason']):raise PermissionDenied
                else:
                    obj=form.save()
                    if action=='holiday':
                        from .models import PublicHoliday
                        if not PublicHoliday.objects.filter(date=obj.date,active=True).exists():
                            from .holidays import flag_requests
                            flag_requests({obj.date},timezone.now())
            return redirect(request.path+'?year='+str(year)+'&saved=1')
    if request.GET.get('saved'):success='저장했습니다.'
    for form,prefix in [(employment,'employment'),(grant,'grant'),(year_form,'year'),(holiday,'holiday')]:
        form.auto_id='leave_'+prefix+'_%s'
    from .holidays import calendar_context
    return render(request,'tasks/leave_settings.html',{'employment_form':employment,'grant_form':grant,'year_form':year_form,'holiday_form':holiday,'year':year,'success':success,'sync_queued':request.GET.get('sync')=='queued','holidays':LeaveHoliday.objects.filter(date__year=year).order_by('date'),'staff':StaffEmployment.objects.select_related('employee__department'),'entries':LeaveEntry.objects.filter(year=year).select_related('employee','actor')[:100],**calendar_context(year)})
