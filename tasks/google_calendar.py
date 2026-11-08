"""Outbound-only integration restricted to an app-created calendar."""
import base64,hashlib,json,secrets
from datetime import timedelta,datetime,timezone as dt_timezone
from urllib.parse import urlencode,quote
from urllib.request import Request,build_opener,HTTPRedirectHandler
from urllib.error import HTTPError,URLError
from cryptography.fernet import Fernet,InvalidToken
from django.conf import settings
from django.db.models import Q
from django.utils import timezone
from .models import GoogleCalendarConnection,CalendarExport,CalendarExportPolicy
from .schedules import card_datetime,SEOUL

SCOPE='https://www.googleapis.com/auth/calendar.app.created'
SCOPES=f'openid email {SCOPE}'


class IntegrationError(Exception):
    def __init__(self,status=0): self.status=status


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs): return None


def request_json(url,method='GET',data=None,token=None,form=False):
    # Callers construct only fixed Google endpoints. No user-supplied hosts/redirects.
    headers={'Accept':'application/json'}
    payload=None
    if data is not None:
        payload=(urlencode(data).encode() if form else json.dumps(data).encode())
        headers['Content-Type']='application/x-www-form-urlencoded' if form else 'application/json'
    if token: headers['Authorization']='Bearer '+token
    try:
        with build_opener(NoRedirect()).open(Request(url,data=payload,headers=headers,method=method),timeout=12) as response:
            body=response.read(2_000_000)
            return json.loads(body) if body else {}
    except HTTPError as exc: raise IntegrationError(exc.code) from None
    except (URLError,ValueError,TimeoutError,OSError): raise IntegrationError() from None


def cipher():
    key=settings.GOOGLE_TOKEN_ENCRYPTION_KEY or base64.urlsafe_b64encode(hashlib.sha256((settings.SECRET_KEY+'workhub-google-token-v1').encode()).digest()).decode()
    return Fernet(key.encode())


def configured(): return bool(settings.GOOGLE_CALENDAR_CLIENT_ID and settings.GOOGLE_CALENDAR_CLIENT_SECRET)


def callback_url(): return settings.WORKHUB_BASE_URL.rstrip('/')+'/work/calendar/google/callback/'


def access_token(connection):
    try: refresh=cipher().decrypt(connection.encrypted_refresh_token.encode()).decode()
    except (InvalidToken,ValueError): raise IntegrationError(401) from None
    data=request_json('https://oauth2.googleapis.com/token','POST',{
        'client_id':settings.GOOGLE_CALENDAR_CLIENT_ID,'client_secret':settings.GOOGLE_CALENDAR_CLIENT_SECRET,
        'refresh_token':refresh,'grant_type':'refresh_token'},form=True)
    if not data.get('access_token'): raise IntegrationError(401)
    return data['access_token']


def official_allowed(user,task):
    return bool(CalendarExportPolicy.objects.filter(pk=1,allow_official=True).exists()
                and user.is_active and task.status not in ['draft','cancelled']
                and (task.assignee.user_id==user.pk or task.participants.filter(user=user,is_active=True).exists()))


def recurrence(rule):
    days=['MO','TU','WE','TH','FR','SA','SU']
    kinds={'daily':'DAILY','weekly':'WEEKLY','weekdays':'WEEKLY','monthly_date':'MONTHLY','monthly_weekday':'MONTHLY','yearly':'YEARLY'}
    parts=[f'FREQ={kinds[rule.kind]}',f'INTERVAL={rule.interval}','WKST=MO']
    start=timezone.localtime(rule.starts_at,SEOUL)
    if rule.kind in ['weekly','weekdays']:
        selected=list(range(5)) if rule.kind=='weekdays' else (rule.weekdays or [start.weekday()])
        parts.append('BYDAY='+','.join(days[n] for n in selected))
    if rule.kind=='monthly_weekday': parts.append(f'BYDAY={rule.week_number}{days[(rule.weekdays or [start.weekday()])[0]]}')
    if rule.kind in ['monthly_date','yearly']:
        if rule.kind=='yearly': parts.append(f'BYMONTH={start.month}')
        if rule.month_day==32: parts.append('BYMONTHDAY=-1')
        elif rule.month_day>28 and rule.month_end=='last':
            parts.extend(['BYMONTHDAY='+','.join(str(n) for n in range(28,rule.month_day+1)),'BYSETPOS=-1'])
        else: parts.append(f'BYMONTHDAY={rule.month_day}')
    if rule.max_count: parts.append(f'COUNT={rule.max_count}')
    if rule.until:
        end=datetime.combine(rule.until,datetime.max.time(),tzinfo=SEOUL).astimezone(dt_timezone.utc)
        # RFC5545 disallows COUNT and UNTIL together; compute the earlier bound.
        if rule.max_count:
            from .schedules import next_run
            current=rule.starts_at;count=1
            while count<rule.max_count:
                nxt=next_run(rule,current)
                if not nxt:break
                current=nxt;count+=1
            parts=[p for p in parts if not p.startswith('COUNT=')]
            parts.append(f'COUNT={count}')
        else: parts.append('UNTIL='+end.strftime('%Y%m%dT%H%M%SZ'))
    return ['RRULE:'+';'.join(parts)]


def payload(export):
    card=export.card
    if card:
        owner=card.column.board.owner
        if owner.pk!=export.user_id or card.calendar_display=='none' or card.state in ['archived','trash']: return None
        if card.series_id and card.series.mode=='calendar' and card.series.template_card_id!=card.pk: return None
        when=card_datetime(card)
        if not when: return None
        title,body=card.title,card.body
        url=f'{settings.WORKHUB_BASE_URL}/work/personal/cards/{card.pk}/'
        if card.due_time:
            start={'dateTime':when.isoformat(),'timeZone':'Asia/Seoul'}
            end={'dateTime':(when+timedelta(minutes=30)).isoformat(),'timeZone':'Asia/Seoul'}
        else:
            start={'date':card.due_date.isoformat()};end={'date':(card.due_date+timedelta(days=1)).isoformat()}
        detail=card.calendar_display
        reminders={'useDefault':False,'overrides':[{'method':'popup','minutes':card.reminder_minutes}] if card.reminder_enabled and card.due_time else []}
    else:
        task=export.task
        if not official_allowed(export.user,task) or export.detail=='none': return None
        title,body=task.title,task.description
        url=f'{settings.WORKHUB_BASE_URL}/tasks/{task.pk}/'
        start={'date':task.due_date.isoformat()};end={'date':(task.due_date+timedelta(days=1)).isoformat()}
        detail=export.detail;reminders={'useDefault':False,'overrides':[]}
    result={'summary':title if detail in ['title','details'] else '업무 일정','start':start,'end':end,
            'visibility':'private','reminders':reminders,'transparency':'transparent'}
    if detail in ['title','details']: result['description']=(body+'\n\n' if detail=='details' else '')+'Workhub에서 보기: '+url
    if card and card.series_id and card.series.mode=='calendar' and card.series.enabled:
        result['recurrence']=recurrence(card.series)
    return result


def sync_export(connection,export,token):
    data=payload(export)
    calendar_id=connection.calendar_id
    event_id=export.event_id if export.calendar_id==calendar_id and export.event_id else 'a'+hashlib.sha256(f'{connection.user_id}:{"card" if export.card_id else "task"}:{export.card_id or export.task_id}'.encode()).hexdigest()
    endpoint='https://www.googleapis.com/calendar/v3/calendars/'+quote(calendar_id,safe='')+'/events/'+event_id
    if data is None:
        if export.event_id and export.calendar_id==calendar_id:
            try: request_json(endpoint,'DELETE',token=token)
            except IntegrationError as exc:
                if exc.status not in [404,410]: raise
        export.event_id='';export.payload_digest='';export.error='';export.save();return
    digest=hashlib.sha256(json.dumps(data,sort_keys=True).encode()).hexdigest()
    if export.event_id and export.calendar_id==calendar_id and export.payload_digest==digest:
        export.save(update_fields=['updated_at']);return
    try: request_json(endpoint,'PUT',data,token)
    except IntegrationError as exc:
        if exc.status not in [404,410]: raise
        try: request_json(endpoint.rsplit('/',1)[0],'POST',{'id':event_id,**data},token)
        except IntegrationError as conflict:
            if conflict.status!=409: raise
            request_json(endpoint,'PUT',data,token)
    export.event_id=event_id;export.calendar_id=calendar_id;export.payload_digest=digest;export.error='';export.save()


def sync_connection(connection):
    if not configured() or not connection.encrypted_refresh_token or connection.status in ['disconnected','reconnect']: return
    try:
        token=access_token(connection)
        exports=CalendarExport.objects.filter(user=connection.user).select_related('card__column__board__owner','task__assignee__user','user')
        for export in exports.order_by('updated_at','pk')[:50]:
            if not GoogleCalendarConnection.objects.filter(pk=connection.pk,encrypted_refresh_token=connection.encrypted_refresh_token,user__is_active=True).exclude(status='disconnected').exclude(user__employee__is_active=False).exists():return
            try: sync_export(connection,export,token)
            except IntegrationError as exc:
                if exc.status!=400:raise
                export.error='일정의 날짜와 내용을 확인한 뒤 다시 저장해 주세요.';export.save(update_fields=['error','updated_at'])
        connection.status='connected';connection.error='일부 일정의 날짜나 내용을 확인해 주세요.' if CalendarExport.objects.filter(user=connection.user).exclude(error='').exists() else '';connection.last_sync_at=timezone.now()
    except IntegrationError as exc:
        connection.status='reconnect' if exc.status in [400,401,403] else 'error'
        connection.error='Google 연결을 다시 확인해 주세요.' if connection.status=='reconnect' else '일시적으로 동기화하지 못했습니다. 다음 처리 때 다시 시도합니다.'
    GoogleCalendarConnection.objects.filter(pk=connection.pk,encrypted_refresh_token=connection.encrypted_refresh_token).exclude(status='disconnected').update(status=connection.status,error=connection.error,last_sync_at=connection.last_sync_at)
