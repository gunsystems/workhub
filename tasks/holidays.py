"""Authoritative Korean holiday ingestion. Failed/partial responses never replace a year."""
import secrets
from datetime import date, timedelta
from decimal import Decimal
from xml.etree import ElementTree as ET
import requests
from django.conf import settings
from django.core.exceptions import ValidationError, PermissionDenied
from django.db import transaction
from django.utils import timezone
from .models import PublicHoliday, HolidaySync, LeaveHoliday, LeaveRequest, LeaveCalendarReview, LeaveEntry

SOURCE_URL='https://www.data.go.kr/data/15012690/openapi.do'
API_URL='https://apis.data.go.kr/B090041/openapi/service/SpcdeInfoService/getRestDeInfo'

def parse_month(content,year,month):
    if len(content)>512_000 or b'<!DOCTYPE' in content.upper():raise ValueError('invalid_response')
    root=ET.fromstring(content)
    if root.findtext('./header/resultCode')!='00':raise ValueError('api_error')
    total=int(root.findtext('./body/totalCount','-1'))
    items=root.findall('./body/items/item')
    if total!=len(items) or not 0<=total<=100:raise ValueError('incomplete_response')
    result={}
    for item in items:
        raw=item.findtext('locdate','');name=item.findtext('dateName','').strip()
        if len(raw)!=8 or not raw.isdigit() or not name or len(name)>100:raise ValueError('invalid_item')
        day=date(int(raw[:4]),int(raw[4:6]),int(raw[6:]))
        if day.year!=year or day.month!=month:raise ValueError('wrong_period')
        if item.findtext('isHoliday') not in ('Y','N'):raise ValueError('invalid_holiday_flag')
        if item.findtext('isHoliday')=='Y':result.setdefault(day,set()).add(name)
    return result

def fetch_year(year,key):
    result={}
    with requests.Session() as session:
        for month in range(1,13):
            response=session.get(API_URL,params={'ServiceKey':key,'solYear':str(year),'solMonth':f'{month:02d}','numOfRows':'100','pageNo':'1'},timeout=(5,10),allow_redirects=False)
            if response.status_code!=200:raise ValueError('http_error')
            result.update(parse_month(response.content,year,month))
    # Unpublished/empty years must not erase the last usable calendar.
    required=[(1,1),(3,1),(5,5),(6,6),(8,15),(10,3),(10,9),(12,25)]
    if year>=2026:required.extend([(5,1),(7,17)])
    if len(result)<15 or any(date(year,m,d) not in result for m,d in required):raise ValueError('year_not_published')
    calendar={day:' · '.join(sorted(names)) for day,names in result.items()}
    if any(len(name)>200 for name in calendar.values()):raise ValueError('invalid_names')
    return calendar

def flag_requests(days,now):
    if not days:return
    for item in LeaveRequest.objects.filter(state__in=['pending','approved','cancel_pending'],start__lte=max(days),end__gte=min(days)):
        relevant=[d.isoformat() for d in sorted(days) if item.start<=d<=item.end and d.weekday()<5]
        if relevant:
            review,_=LeaveCalendarReview.objects.get_or_create(request=item)
            previous=review.changed_dates if review.pending else []
            review.pending=True;review.changed_dates=sorted(set(previous+relevant));review.updated_at=now;review.save()

@transaction.atomic
def apply_year(year,calendar,now):
    existing={h.date:h for h in PublicHoliday.objects.filter(date__year=year)}
    company=set(LeaveHoliday.objects.filter(date__year=year).values_list('date',flat=True))
    old=set(day for day,h in existing.items() if h.active)|company
    new=set(calendar)|company
    for day,name in calendar.items():
        PublicHoliday.objects.update_or_create(date=day,defaults={'name':name,'active':True,'updated_at':now})
    PublicHoliday.objects.filter(date__year=year).exclude(date__in=calendar).update(active=False,updated_at=now)
    flag_requests(old^new,now)
    from .audit import write
    write('update','PublicHolidayYear',year,'공휴일 자동 갱신',after={'year':year,'count':len(calendar),'changed_dates':[d.isoformat() for d in sorted(old^new)],'source':SOURCE_URL})

def queue_year(year):
    row,_=HolidaySync.objects.get_or_create(year=year)
    row.next_attempt=timezone.now();row.save(update_fields=['next_attempt'])

def synchronize_due(now=None):
    now=now or timezone.now()
    if not getattr(settings,'WORKHUB_HOLIDAY_SYNC_ENABLED',True):return
    for year in [timezone.localdate(now).year,timezone.localdate(now).year+1]:HolidaySync.objects.get_or_create(year=year,defaults={'next_attempt':now,'lease_until':now})
    # An independent worker and per-year lease keep external calls out of login/notification work.
    for pk in HolidaySync.objects.filter(next_attempt__lte=now,lease_until__lte=now).values_list('pk',flat=True)[:4]:
        token=secrets.token_hex(24)
        if not HolidaySync.objects.filter(pk=pk,next_attempt__lte=now,lease_until__lte=now).update(token=token,lease_until=now+timedelta(minutes=5),last_attempt=now):continue
        row=HolidaySync.objects.get(pk=pk)
        key=getattr(settings,'KASI_SERVICE_KEY','').strip()
        if not key:
            HolidaySync.objects.filter(pk=pk,token=token).update(state='configuration',message='공공데이터포털 인증키 연결이 필요합니다.',next_attempt=now+timedelta(hours=1),lease_until=now)
            continue
        try:
            calendar=fetch_year(row.year,key)
            with transaction.atomic():
                # Conditional claim makes a superseded/expired worker harmless.
                if not HolidaySync.objects.filter(pk=pk,token=token,lease_until__gt=timezone.now()).update(state='ready',message='',last_success=timezone.now(),next_attempt=timezone.now()+timedelta(hours=24),lease_until=timezone.now()):continue
                apply_year(row.year,calendar,timezone.now())
        except Exception:
            # Never persist exception text: HTTP exceptions may embed the service key.
            HolidaySync.objects.filter(pk=pk,token=token).update(state='error',message='공식 자료를 확인하지 못했습니다. 기존 목록을 유지하고 다시 확인합니다.',next_attempt=timezone.now()+timedelta(hours=1),lease_until=timezone.now())

def calendar_context(year):
    row=HolidaySync.objects.filter(year=year).first()
    from .leave import amount
    reviews=[]
    for review in LeaveCalendarReview.objects.filter(pending=True,request__start__year=year).select_related('request__employee__department'):
        value,_=amount(review.request.start,review.request.end,review.request.kind,allow_empty=True)
        reviews.append({'review':review,'days':value})
    return {'holiday_sync':row,'public_holidays':PublicHoliday.objects.filter(date__year=year,active=True).order_by('date'),
            'holiday_source':SOURCE_URL,'holiday_key_ready':bool(getattr(settings,'KASI_SERVICE_KEY','').strip()),
            'holiday_reviews':reviews}

@transaction.atomic
def reconcile(pk,user,revision):
    """Explicit administrator reconciliation retains the original ledger and event trail."""
    if not user.is_active or not user.is_superuser:raise PermissionDenied
    from . import leave
    item=LeaveRequest.objects.select_for_update().get(pk=pk)
    review=LeaveCalendarReview.objects.select_for_update().get(request=item)
    if item.revision!=revision or not review.pending:raise ValidationError('처리 상태가 변경되었습니다. 새로고침 후 확인하세요.')
    if item.state in ('pending','approved','cancel_pending'):
        value,dates=leave.amount(item.start,item.end,item.kind,allow_empty=True)
        if LeaveRequest.objects.filter(pk=pk,revision=revision).update(revision=revision+1)!=1:raise ValidationError('이미 반영된 요청입니다.')
        before=item.days;before_dates=list(item.dates);item.days=value;item.dates=dates;item.revision+=1
        if item.state in ('approved','cancel_pending') and before!=value:
            LeaveEntry.objects.create(employee=item.employee,year=item.start.year,amount=before-value,actor=user,reason='휴무일 변경에 따른 승인 일수 조정',key=f'holiday:{pk}:{item.revision}')
        if item.state=='pending' and (before!=value or before_dates!=dates):
            if not value:item.state='withdrawn'
            else:
                latest=item.steps.order_by('-round').values_list('round',flat=True).first() or 0
                chain=[(s.role,s.approver,s.delegated) for s in item.steps.filter(round=latest)]
                leave.steps(item,chain,latest+1)
        item.save(update_fields=['days','dates','revision','state'])
        leave.event(item,user,'휴무일 변경 반영',f'{before}일 → {value}일. 기존 사용일: '+', '.join(before_dates)+' / 변경 사용일: '+', '.join(dates))
    review.pending=False;review.save(update_fields=['pending'])
