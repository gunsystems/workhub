"""Delegated Graph integration. Only Workhub-created events are mutated."""
import base64, hashlib, json, secrets, uuid, logging, socket
from datetime import timedelta, timezone as dt_timezone
from urllib.parse import urlencode, quote, urlsplit
from urllib.request import Request, build_opener
from urllib.error import HTTPError, URLError
from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST
from .google_calendar import NoRedirect
from .models import MicrosoftConnection, Meeting

SCOPES = 'offline_access User.Read Calendars.ReadWrite'
logger = logging.getLogger(__name__)


class GraphError(Exception):
    def __init__(self, status=0, reason='unknown'):
        self.status = status
        self.reason = reason


def configured():
    try:
        uuid.UUID(settings.MICROSOFT_TENANT_ID); uuid.UUID(settings.MICROSOFT_CLIENT_ID)
    except (ValueError, TypeError, AttributeError): return False
    return bool(settings.MICROSOFT_CLIENT_SECRET)


def callback_url(): return settings.WORKHUB_BASE_URL.rstrip('/')+'/work/meetings/microsoft/callback/'


def cipher():
    key = settings.MICROSOFT_TOKEN_ENCRYPTION_KEY or base64.urlsafe_b64encode(hashlib.sha256((settings.SECRET_KEY+'workhub-microsoft-v1').encode()).digest()).decode()
    return Fernet(key.encode())


def request_json(path, method='GET', data=None, token=None, oauth=False):
    # No arbitrary hosts or redirects, including provider-returned web URLs.
    url = ('https://login.microsoftonline.com/'+settings.MICROSOFT_TENANT_ID+'/oauth2/v2.0/token') if oauth else 'https://graph.microsoft.com/v1.0'+path
    body = (urlencode(data).encode() if oauth else json.dumps(data).encode()) if data is not None else None
    headers = {'Accept':'application/json','Content-Type':'application/x-www-form-urlencoded' if oauth else 'application/json'}
    if token: headers['Authorization'] = 'Bearer '+token
    try:
        with build_opener(NoRedirect()).open(Request(url, data=body, headers=headers, method=method), timeout=12) as response:
            raw = response.read(2_000_001)
            if len(raw)>2_000_000: raise ValueError
            return json.loads(raw) if raw else {}
    except HTTPError as exc: raise GraphError(exc.code, 'http') from None
    except (TimeoutError, socket.timeout): raise GraphError(0, 'timeout') from None
    except URLError as exc:
        raise GraphError(0, 'timeout' if isinstance(exc.reason, (TimeoutError, socket.timeout)) else 'network') from None
    except OSError: raise GraphError(0, 'network') from None
    except ValueError: raise GraphError(0, 'response') from None


def token_request(data):
    return request_json('', 'POST', {'client_id':settings.MICROSOFT_CLIENT_ID, 'client_secret':settings.MICROSOFT_CLIENT_SECRET, **data}, oauth=True)


def access_token(connection):
    try: refresh = cipher().decrypt(connection.encrypted_refresh_token.encode()).decode()
    except (InvalidToken, ValueError): raise GraphError(401) from None
    result = token_request({'refresh_token':refresh, 'grant_type':'refresh_token','scope':SCOPES})
    if not result.get('access_token'): raise GraphError(401)
    if result.get('refresh_token'):
        MicrosoftConnection.objects.filter(pk=connection.pk).update(encrypted_refresh_token=cipher().encrypt(result['refresh_token'].encode()).decode())
    return result['access_token']


@login_required
@never_cache
def settings_view(request):
    return render(request, 'tasks/microsoft_settings.html', {'configured':configured(), 'callback_url':callback_url(),
        'connection':MicrosoftConnection.objects.filter(user=request.user).first()})


@login_required
@require_POST
@never_cache
def connect(request):
    if not configured():
        messages.error(request,'관리자의 Microsoft 연결 설정이 필요합니다.'); return redirect('microsoft_settings')
    nonce=secrets.token_urlsafe(32); verifier=secrets.token_urlsafe(64)
    request.session['microsoft_oauth']={'state':nonce,'verifier':verifier,'user':request.user.pk,'at':timezone.now().timestamp()}
    challenge=base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip('=')
    return redirect('https://login.microsoftonline.com/'+settings.MICROSOFT_TENANT_ID+'/oauth2/v2.0/authorize?'+urlencode({
        'client_id':settings.MICROSOFT_CLIENT_ID,'redirect_uri':callback_url(),'response_type':'code','response_mode':'query',
        'scope':SCOPES,'state':nonce,'code_challenge':challenge,'code_challenge_method':'S256','prompt':'select_account'}))


@login_required
@never_cache
def callback(request):
    stage='state'
    state=request.session.pop('microsoft_oauth',{})
    valid=(configured() and state.get('user')==request.user.pk and 0<=timezone.now().timestamp()-state.get('at',0)<600
        and bool(state.get('state')) and secrets.compare_digest(state.get('state',''),request.GET.get('state','')))
    try:
        if not valid or request.GET.get('error') or not request.GET.get('code'): raise GraphError()
        stage='token'
        result=token_request({'grant_type':'authorization_code','code':request.GET['code'],'code_verifier':state['verifier'],'redirect_uri':callback_url()})
        stage='scope'
        scopes={s.lower() for s in result.get('scope','').split()}
        if not result.get('refresh_token') or not any(s.endswith('calendars.readwrite') for s in scopes): raise GraphError()
        stage='profile'
        person=request_json('/me?$select=id,mail,userPrincipalName',token=result['access_token'])
        stage='email'
        email=request.user.email.strip().lower()
        if not email or email not in {str(person.get(k,'') or '').lower() for k in ['mail','userPrincipalName']} or not person.get('id'): raise GraphError()
        stage='calendar'
        calendar=request_json('/me/calendar?$select=allowedOnlineMeetingProviders',token=result['access_token'])
        stage='teams_provider'
        if 'teamsForBusiness' not in calendar.get('allowedOnlineMeetingProviders',[]): raise GraphError()
        old=MicrosoftConnection.objects.filter(user=request.user).first()
        if old and old.subject!=person['id'] and Meeting.objects.filter(organizer=request.user).exclude(event_id='').exists(): raise GraphError()
        MicrosoftConnection.objects.update_or_create(user=request.user,defaults={'subject':person['id'],'email':email,
            'encrypted_refresh_token':cipher().encrypt(result['refresh_token'].encode()).decode()})
        messages.success(request,'Microsoft 계정을 연결했습니다. 미팅 등록에서 Teams 자동 생성을 선택할 수 있습니다.')
    except (GraphError, KeyError, ValueError) as exc:
        # Never log tokens, response bodies, URLs, emails or exception text.
        logger.warning('Microsoft connection failed stage=%s status=%s reason=%s',stage,getattr(exc,'status',0),getattr(exc,'reason','validation'))
        detail = {
            'state':'연결 요청이 만료되었거나 유효하지 않습니다. 회사 Microsoft 계정 연결을 다시 눌러 주세요.',
            'email':'Workhub와 동일한 회사 이메일의 Microsoft 계정으로 연결해 주세요.',
            'teams_provider':'Microsoft 일정에서 Teams 회의를 만들 수 있는 계정인지 확인해 주세요.',
        }.get(stage,'Microsoft 연결을 완료하지 못했습니다. 잠시 후 다시 시도해 주세요.')
        if getattr(exc,'reason','')=='timeout': detail='Microsoft 응답 시간이 초과되었습니다. 잠시 후 다시 연결해 주세요.'
        messages.error(request,detail)
    response=redirect('microsoft_settings'); response['Referrer-Policy']='no-referrer'; return response


@login_required
@require_POST
def disconnect(request):
    from django.db.models import Q
    if Meeting.objects.filter(organizer=request.user,provider='teams',task__is_deleted=False).filter(
        ~Q(task__status__in=['cancelled','done']) | ~Q(sync_state='synced')).exists():
        messages.error(request,'Teams로 연결된 미팅을 먼저 취소해 주세요. 연결 해제 시 일정 변경·취소를 전달할 수 없습니다.')
    else:
        MicrosoftConnection.objects.filter(user=request.user).delete()
        messages.success(request,'Workhub의 Microsoft 연결을 해제했습니다.')
    return redirect('microsoft_settings')


def valid_join_url(value):
    parts=urlsplit(value)
    return parts.scheme=='https' and parts.hostname in ['teams.microsoft.com','teams.live.com','teams.cloud.microsoft'] and not parts.username and not parts.password


def sync_one(pk):
    now=timezone.now()
    if not Meeting.objects.filter(pk=pk,sync_state='pending').update(sync_state='sending',claimed_at=now): return
    item=Meeting.objects.select_related('task','organizer').get(pk=pk)
    revision=item.revision; state='synced'; error=''; event_id=item.event_id; join_url=item.join_url
    try:
        from accounts.lifecycle import account_allowed
        if not account_allowed(item.organizer): raise GraphError(401)
        connection=MicrosoftConnection.objects.get(user=item.organizer)
        token=access_token(connection)
        path='/me/events/'+quote(event_id,safe='') if event_id else '/me/events'
        if item.task.is_deleted or item.task.status=='cancelled':
            if event_id:
                try: request_json(path,'DELETE',token=token)
                except GraphError as exc:
                    if exc.status!=404: raise
        else:
            attendees=[{'emailAddress':{'address':r.employee.user.email,'name':r.employee.name},'type':'required'} for r in item.attendees.filter(active=True,employee__is_active=True,employee__user__is_active=True).select_related('employee__user') if r.employee.user_id!=item.organizer_id]
            attendees += [{'emailAddress':{'address':email},'type':'required'} for email in item.guests]
            payload={'subject':item.task.title,'start':{'dateTime':item.task.activity_start.astimezone(dt_timezone.utc).replace(tzinfo=None).isoformat(),'timeZone':'UTC'},
                'end':{'dateTime':item.task.activity_end.astimezone(dt_timezone.utc).replace(tzinfo=None).isoformat(),'timeZone':'UTC'},'location':{'displayName':item.task.location},'attendees':attendees,
                'isReminderOn':True,'reminderMinutesBeforeStart':15}
            if not event_id:
                payload.update(isOnlineMeeting=True,onlineMeetingProvider='teamsForBusiness',transactionId=str(item.transaction_id),
                    body={'contentType':'text','content':item.task.description})
            # Preserve the provider-generated Teams body when updating an event.
            result=request_json(path,'PATCH' if event_id else 'POST',payload,token)
            event_id=result.get('id') or event_id
            if not event_id: raise GraphError()
            url=(result.get('onlineMeeting') or {}).get('joinUrl') or join_url
            if not url:
                result=request_json('/me/events/'+quote(event_id,safe='')+'?$select=onlineMeeting',token=token)
                url=(result.get('onlineMeeting') or {}).get('joinUrl','')
            if not valid_join_url(url): raise GraphError()
            join_url=url
    except (GraphError, MicrosoftConnection.DoesNotExist) as exc:
        status=getattr(exc,'status',401)
        state='unknown' if status==0 else 'error'
        error='처리 결과를 확인하지 못했습니다. Microsoft 일정 확인 후 다시 연결하세요.' if state=='unknown' else 'Microsoft 연결·권한을 확인한 뒤 다시 시도해 주세요.'
    Meeting.objects.filter(pk=pk,sync_state='sending').update(event_id=event_id,join_url=join_url,sync_state=state,sync_error=error,
        synced_revision=revision if state=='synced' else item.synced_revision,claimed_at=None)
    if state=='synced':
        Meeting.objects.filter(pk=pk,revision__gt=revision).update(sync_state='pending')


def synchronize():
    # A crashed/ambiguous write is never automatically repeated as a new invitation.
    Meeting.objects.filter(sync_state='sending',claimed_at__lt=timezone.now()-timedelta(minutes=5)).update(sync_state='unknown',sync_error='처리 결과를 확인해 주세요.')
    for pk in Meeting.objects.filter(provider='teams',sync_state='pending').values_list('pk',flat=True)[:10]: sync_one(pk)
