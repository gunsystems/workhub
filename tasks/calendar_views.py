import base64,hashlib,secrets
from datetime import timedelta
from urllib.parse import urlencode,quote
from django import forms
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.shortcuts import redirect,render,get_object_or_404
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST
from . import google_calendar as google
from .models import GoogleCalendarConnection,CalendarExportPolicy,CalendarExport,Task,PersonalSchedule,PersonalReminder,SchedulerLease
from .permissions import visible_tasks,is_company_admin
from .schedules import next_run


@login_required
@never_cache
def settings_view(request):
    connection=GoogleCalendarConnection.objects.filter(user=request.user).first()
    return render(request,'tasks/calendar_settings.html',{'connection':connection,'configured':google.configured(),
        'callback_url':google.callback_url(),'policy':CalendarExportPolicy.objects.filter(pk=1).first(),
        'scheduler':SchedulerLease.objects.filter(key='personal').first()})


@login_required
@require_POST
@never_cache
def connect(request):
    if not google.configured():
        messages.info(request,'관리자의 Google 연동 앱 설정이 필요합니다.');return redirect('calendar_settings')
    nonce=secrets.token_urlsafe(32);verifier=secrets.token_urlsafe(64)
    request.session['google_oauth']={'state':nonce,'verifier':verifier,'user':request.user.pk,'at':timezone.now().timestamp()}
    challenge=base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip('=')
    return redirect('https://accounts.google.com/o/oauth2/v2/auth?'+urlencode({
        'client_id':settings.GOOGLE_CALENDAR_CLIENT_ID,'redirect_uri':google.callback_url(),'response_type':'code',
        'scope':google.SCOPES,'state':nonce,'access_type':'offline','prompt':'consent select_account',
        'code_challenge':challenge,'code_challenge_method':'S256'}))


@login_required
@never_cache
def callback(request):
    state=request.session.pop('google_oauth',{})
    valid=state.get('user')==request.user.pk and 0<=timezone.now().timestamp()-state.get('at',0)<600 and secrets.compare_digest(state.get('state',''),request.GET.get('state',''))
    if not valid or not state.get('state') or request.GET.get('error') or not request.GET.get('code'):
        messages.error(request,'Google 연결을 확인하지 못했습니다. 연결 버튼에서 다시 시작해 주세요.');return redirect('calendar_settings')
    try:
        token=google.request_json('https://oauth2.googleapis.com/token','POST',{'client_id':settings.GOOGLE_CALENDAR_CLIENT_ID,
            'client_secret':settings.GOOGLE_CALENDAR_CLIENT_SECRET,'code':request.GET['code'],'code_verifier':state['verifier'],
            'redirect_uri':google.callback_url(),'grant_type':'authorization_code'},form=True)
        if not token.get('refresh_token') or google.SCOPE not in token.get('scope','').split(): raise google.IntegrationError()
        profile=google.request_json('https://openidconnect.googleapis.com/v1/userinfo',token=token['access_token'])
        if not profile.get('sub') or not profile.get('email') or profile.get('email_verified') is not True: raise google.IntegrationError()
        old=GoogleCalendarConnection.objects.filter(user=request.user).first()
        calendar_id=old.calendar_id if old and old.subject==profile['sub'] else ''
        if calendar_id:
            try: google.request_json('https://www.googleapis.com/calendar/v3/calendars/'+quote(calendar_id,safe=''),token=token['access_token'])
            except google.IntegrationError as exc:
                if exc.status not in [404,410]:raise
                calendar_id=''
        if not calendar_id:
            result=google.request_json('https://www.googleapis.com/calendar/v3/calendars','POST',{'summary':'Workhub','timeZone':'Asia/Seoul'},token['access_token'])
            calendar_id=result['id']
        GoogleCalendarConnection.objects.update_or_create(user=request.user,defaults={'subject':profile['sub'],'email':profile['email'],
            'encrypted_refresh_token':google.cipher().encrypt(token['refresh_token'].encode()).decode(),
            'calendar_id':calendar_id,'status':'connected','error':''})
        messages.success(request,'Google 계정을 연결했습니다. 선택한 일정부터 Workhub 전용 캘린더로 동기화합니다.')
    except (google.IntegrationError,KeyError,ValueError): messages.error(request,'Google 연결을 완료하지 못했습니다. 잠시 후 다시 연결해 주세요.')
    response=redirect('calendar_settings');response['Referrer-Policy']='no-referrer';return response


@login_required
@require_POST
@never_cache
def disconnect(request):
    connection=GoogleCalendarConnection.objects.filter(user=request.user).first()
    if connection:
        try:
            refresh=google.cipher().decrypt(connection.encrypted_refresh_token.encode()).decode()
            google.request_json('https://oauth2.googleapis.com/revoke','POST',{'token':refresh},form=True)
        except Exception: pass
        connection.encrypted_refresh_token='';connection.status='disconnected';connection.error='';connection.save()
    messages.success(request,'연결을 해제했습니다. 이미 Google에 생성된 일정은 남아 있으며 Google Calendar에서 정리할 수 있습니다.')
    return redirect('calendar_settings')


@login_required
@require_POST
def policy(request):
    if not is_company_admin(request.user): raise PermissionDenied
    CalendarExportPolicy.objects.update_or_create(pk=1,defaults={'allow_official':request.POST.get('allow_official')=='on'})
    return redirect('calendar_settings')


class ExportForm(forms.Form):
    detail=forms.ChoiceField(label='Google 캘린더에 무엇을 등록할까요?',widget=forms.RadioSelect,choices=[('minimal','마감일만 등록'),('title','업무 제목도 등록'),('details','업무 제목과 설명까지 등록')])


def task_calendar_registered(user,task):
    return CalendarExport.objects.filter(user=user,task=task).exclude(detail='none').exists()


@login_required
@never_cache
def export_task(request,pk):
    task=get_object_or_404(visible_tasks(request.user),pk=pk)
    old=CalendarExport.objects.filter(user=request.user,task=task).first()
    if request.method=='POST' and request.POST.get('action')=='remove':
        CalendarExport.objects.filter(user=request.user,task=task).update(detail='none')
        messages.success(request,'이 업무의 Google 일정 제거를 요청했습니다. 다음 동기화에 반영되며 Workhub 업무는 유지됩니다.')
        return redirect('google_export_task' if google.official_allowed(request.user,task) else 'task_detail',pk=pk)
    if not google.official_allowed(request.user,task): raise PermissionDenied
    registered=bool(old and old.detail!='none')
    form=ExportForm(request.POST if request.method=='POST' else None,initial={'detail':old.detail if registered else 'minimal'})
    if task.activity_start:
        form.fields['detail'].choices=[('minimal','일시만 등록 · 제목은 업무 일정'),('title','업무 제목과 일시 등록'),('details','업무 제목·일시·설명 등록')]
    if request.method=='POST' and form.is_valid():
        CalendarExport.objects.update_or_create(user=request.user,task=task,defaults={'detail':form.cleaned_data['detail']})
        messages.success(request,'등록 설정을 저장했습니다. Google 계정이 연결돼 있으면 다음 동기화에 반영됩니다.')
        return redirect('google_export_task',pk=pk)
    return render(request,'tasks/calendar_task_form.html',{'form':form,'task':task,'registered':registered,'export':old,
        'connection':GoogleCalendarConnection.objects.filter(user=request.user).first()})


@login_required
@require_POST
@transaction.atomic
def toggle_schedule(request,pk):
    rule=get_object_or_404(PersonalSchedule.objects.select_for_update(),pk=pk,template_card__column__board__owner=request.user)
    rule.enabled=not rule.enabled
    if not rule.enabled: rule.next_at=None
    elif rule.mode=='calendar': rule.next_at=next_run(rule,timezone.now()-timedelta(seconds=1))
    rule.save()
    return redirect('personal_card_edit',pk=rule.template_card_id)


@login_required
@require_POST
def read_reminder(request,pk):
    item=get_object_or_404(PersonalReminder,pk=pk,card__column__board__owner=request.user)
    item.read_at=timezone.now();item.save(update_fields=['read_at'])
    return redirect('personal_card_edit',pk=item.card_id)
