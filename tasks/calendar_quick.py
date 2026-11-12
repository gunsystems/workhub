"""Minimal calendar entry delegates creation to the existing official work workflow."""
import copy,hashlib,secrets
from datetime import datetime,timedelta
from zoneinfo import ZoneInfo
from django import forms
from django.contrib.auth.decorators import login_required
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST
from django.core import signing
from django.core.exceptions import PermissionDenied
from django.db import transaction,OperationalError
from django.http import QueryDict
from django.shortcuts import render,redirect
from django.utils import timezone
from .models import Employee,WorkCategory,CalendarQuickSave
from .permissions import profile,is_company_admin,visible_employees
from .collaboration import edit_task

def entry_token(user):return signing.dumps({'user':user.pk,'nonce':secrets.token_hex(20)},salt='calendar-quick')

class CalendarQuickForm(forms.Form):
    title=forms.CharField(label='일정명',max_length=160)
    start_date=forms.DateField(label='시작일',widget=forms.DateInput(attrs={'type':'date'}))
    due_date=forms.DateField(label='종료일',widget=forms.DateInput(attrs={'type':'date'}))
    all_day=forms.BooleanField(label='종일',required=False,initial=True)
    start_time=forms.TimeField(label='시작 시간',required=False,widget=forms.TimeInput(attrs={'type':'time'}))
    end_time=forms.TimeField(label='종료 시간',required=False,widget=forms.TimeInput(attrs={'type':'time'}))
    assignee=forms.ModelChoiceField(label='담당자',queryset=Employee.objects.none())
    category=forms.ModelChoiceField(label='업무 종류',queryset=WorkCategory.objects.none(),required=False,empty_label='일반 업무')
    location=forms.CharField(label='장소',max_length=200,required=False)
    description=forms.CharField(label='설명',max_length=10000,required=False,widget=forms.Textarea(attrs={'rows':3}))
    assignment_email=forms.BooleanField(label='이메일로 배정 알림',required=False,initial=True)
    assignment_messenger=forms.BooleanField(label='하이웍스로 배정 알림',required=False,initial=True)
    token=forms.CharField(widget=forms.HiddenInput)
    def __init__(self,*args,user,details=False,**kwargs):
        super().__init__(*args,**kwargs)
        self.fields['assignee'].queryset=visible_employees(user).filter(is_active=True).exclude(user__is_active=False).select_related('department')
        self.fields['category'].queryset=WorkCategory.objects.filter(is_active=True)
        self.fields['title'].required=not details;self.fields['assignee'].required=not details
    def clean(self):
        data=super().clean();start=data.get('start_date');end=data.get('due_date')
        if start and end and end<start:self.add_error('due_date','종료일은 시작일 이후로 선택하세요.')
        if not data.get('all_day'):
            first=data.get('start_time');last=data.get('end_time')
            if first is None:self.add_error('start_time','시작 시간을 입력하세요.')
            if last is None:self.add_error('end_time','종료 시간을 입력하세요.')
            if start and end and first and last and datetime.combine(end,last)<=datetime.combine(start,first):self.add_error('end_time','종료는 시작보다 늦어야 합니다.')
        return data

def prefill(data):
    result={k:data.get(k) for k in ['title','start_date','due_date','assignee','category','location','description','assignment_email','assignment_messenger']}
    result.update(management_mode='event',no_approval=True,allocation='shared',status='todo',priority=3,importance='normal',revision=0,purpose='self')
    result['assignee']=data['assignee'].pk if data.get('assignee') else None
    result['category']=data['category'].pk if data.get('category') else None
    if not data.get('all_day'):
        result['start_time']=data['start_time']
        for field,d,t in [('activity_start',data['start_date'],data['start_time']),('activity_end',data['due_date'],data['end_time'])]:result[field]=timezone.make_aware(datetime.combine(d,t),ZoneInfo('Asia/Seoul'))
    else:result['start_time']=None
    return result

@login_required
@never_cache
@require_POST
def create(request):
    if not is_company_admin(request.user) and not profile(request.user):raise PermissionDenied
    details=request.POST.get('action')=='details'
    form=CalendarQuickForm(request.POST,user=request.user,details=details)
    if form.is_valid():
        try:
            token=signing.loads(form.cleaned_data['token'],salt='calendar-quick',max_age=86400)
            if token.get('user')!=request.user.pk:raise signing.BadSignature
        except signing.BadSignature:
            form.add_error(None,'등록 화면의 유효 시간이 지났습니다. 캘린더에서 등록 창을 다시 열어 주세요.')
        else:
            proxy=copy.copy(request)
            proxy._calendar_quick=True
            initial=prefill(form.cleaned_data)
            initial['purpose']='self' if form.cleaned_data.get('assignee') and form.cleaned_data['assignee'].user_id==request.user.pk else 'request'
            if details:
                proxy.method='GET';proxy.GET=QueryDict('')
                return edit_task(proxy,calendar_prefill=initial)
            data=QueryDict('',mutable=True)
            for key,value in initial.items():
                if value is None:continue
                if key in ['activity_start','activity_end']:value=value.strftime('%Y-%m-%dT%H:%M')
                data[key]='true' if value is True else 'false' if value is False else str(value)
            data['assignment_channels_present']='1';proxy.POST=data
            fingerprint=hashlib.sha256(data.urlencode().encode()).hexdigest()
            try:
                with transaction.atomic():
                    saved,created=CalendarQuickSave.objects.get_or_create(key=hashlib.sha256(form.cleaned_data['token'].encode()).hexdigest(),defaults={'actor_id_snapshot':request.user.pk,'fingerprint':fingerprint})
                    if not created:
                        if saved.fingerprint==fingerprint and saved.task_id and not saved.task.is_deleted:return redirect('task_detail',pk=saved.task_id)
                        form.add_error(None,'이미 처리된 등록입니다. 캘린더에서 현재 일정을 확인해 주세요.')
                    else:
                        def capture(obj):saved.task=obj;saved.save(update_fields=['task'])
                        response=edit_task(proxy,on_created=capture)
                        if response.status_code==302:return response
                        transaction.set_rollback(True)
                        return response
            except OperationalError:form.add_error(None,'다른 처리가 진행 중입니다. 잠시 후 다시 저장해 주세요.')
    return render(request,'tasks/calendar_quick_error.html',{'form':form})
