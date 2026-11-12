"""Year-based leave ledger and snapshotted, sequential approval."""
from datetime import date, timedelta
from decimal import Decimal
from django.core.exceptions import ValidationError, PermissionDenied
from django.db import transaction
from django.db.models import Q, Sum
from django.utils import timezone
from .models import Employee, StaffEmployment, LeaveYear, LeaveHoliday, LeaveEntry, LeaveRequest, LeaveApproval, LeaveEvent, PublicHoliday, HolidaySync
from .permissions import profile, can_view_company

def visible(user):
    e=profile(user)
    qs=LeaveRequest.objects.select_related('employee__department')
    if can_view_company(user): return qs
    if not e:return qs.none()
    allowed=Q(employee=e)|Q(steps__approver=e)
    if e.position=='leader' or e.role=='manager':allowed|=Q(employee__department=e.department)
    return qs.filter(allowed).distinct()

def balance(employee,year):
    return LeaveEntry.objects.filter(employee=employee,year=year).aggregate(v=Sum('amount'))['v'] or Decimal('0')

def reserved(employee,year):
    return LeaveRequest.objects.filter(employee=employee,start__year=year,state='pending').aggregate(v=Sum('days'))['v'] or Decimal('0')

def entitlement_preview(employee, year):
    """Jan-1 tenure reference, never an attendance or legal-entitlement decision."""
    employment = getattr(employee, 'employment', None)
    if employee.position == 'executive':
        return {'days': None, 'note': '대표 · 적용 여부 별도 확인'}
    if not employment:
        return {'days': None, 'note': '입사일 등록 필요'}
    opening = date(year, 1, 1)
    if employment.ended_on and employment.ended_on < opening:
        return {'days': None, 'note': '운영 연도 이전 퇴사'}
    joined = employment.joined_on
    years = year - joined.year - ((1, 1) < (joined.month, joined.day))
    if years < 1:
        return {'days': None, 'note': '월 개근 발생분·첫해 비례 부여 별도 확인'}
    if employment.ended_on:
        return {'days': None, 'note': '퇴사 예정 · 입사일 기준 정산 확인'}
    return {'days': min(25, 15 + max(0, (years - 1) // 2)),
            'note': f'1월 1일 기준 만 {years}년 · 전년도 출근율 80% 이상 가정'}


def preparation(year):
    """Read-only opening checklist; never infer entitlement from tenure alone."""
    staff=Employee.objects.filter(is_active=True).select_related('department','user','employment')
    rows=[]
    entries={}
    for entry in LeaveEntry.objects.filter(year=year):
        data=entries.setdefault(entry.employee_id,{'granted':Decimal('0'),'used':Decimal('0'),'has_grant':False})
        if entry.key.startswith(('leave:', 'holiday:')):
            data['used']-=entry.amount
        else:
            data['granted']+=entry.amount
            data['has_grant']=True
    for employee in staff:
        issues=[]
        employment=getattr(employee,'employment',None)
        ledger=entries.get(employee.pk,{'granted':Decimal('0'),'used':Decimal('0'),'has_grant':False})
        if not employment:issues.append('입사일 등록 필요')
        elif employment.ended_on and employment.ended_on.year<year:issues.append('재직 상태 확인 필요')
        elif employment.joined_on.year>year:issues.append('해당 연도 입사 전')
        if not employee.user_id or not employee.user.is_active:issues.append('사용 가능한 로그인 계정 연결 필요')
        chain=[]
        representative=employee.position=='executive'
        if not representative:
            try:chain=route(employee)
            except ValidationError as exc:issues.extend(exc.messages)
        if not representative and not ledger['has_grant']:issues.append('연차 부여·적용 대상 확인 필요')
        rows.append({'employee':employee,'employment':employment,'chain':chain,'issues':issues,
                     'representative':representative,'entitlement':entitlement_preview(employee,year),**ledger,'balance':ledger['granted']-ledger['used']})
    calendar=LeaveYear.objects.filter(year=year).first()
    from django.conf import settings
    source_ready=not getattr(settings,'WORKHUB_HOLIDAY_SYNC_ENABLED',True) or HolidaySync.objects.filter(year=year,last_success__gte=timezone.now()-timedelta(hours=48)).exists()
    return {'rows':rows,'year':year,'calendar':calendar,
            'calendar_ready':bool(calendar and calendar.calendar_confirmed and source_ready),
            'holiday_count':LeaveHoliday.objects.filter(date__year=year).count(),
            'needs_count':sum(bool(row['issues']) for row in rows)}

def route(employee):
    def one(position,team=None):
        qs=Employee.objects.filter(is_active=True,position=position,user__is_active=True)
        if team:qs=qs.filter(department=team)
        people=list(qs[:2])
        if len(people)>1:raise ValidationError('결재 담당자가 여러 명입니다. 관리자에게 결재 역할 정리를 요청하세요.')
        return people[0] if people else None
    if employee.position=='executive':raise ValidationError('대표 본인의 휴가 결재 기준은 아직 설정되지 않았습니다.')
    rep=one('executive')
    if not rep:raise ValidationError('대표 결재 담당자를 먼저 지정해 주세요.')
    result=[]
    if employee.position=='member':
        leader=one('leader',employee.department)
        if not leader:raise ValidationError('소속 팀의 팀장 결재 담당자를 먼저 지정해 주세요.')
        result.append(('leader',leader,False))
    if employee.position!='head':
        head=one('head')
        result.append(('head',head or rep,not bool(head)))
    result.append(('executive',rep,False))
    if any(person.pk==employee.pk for _,person,_ in result):raise ValidationError('본인이 결재자에 포함되어 신청할 수 없습니다.')
    return result

def amount(start,end,kind,allow_empty=False):
    if end<start or start.year!=end.year:raise ValidationError('같은 연도 안에서 올바른 시작일·종료일을 선택하세요. 연도별로 나누어 신청할 수 있습니다.')
    if (end-start).days>366:raise ValidationError('신청 기간을 확인하세요.')
    if kind not in ('annual','am','pm'):raise ValidationError('휴가 종류를 확인하세요.')
    if kind!='annual' and start!=end:raise ValidationError('반차는 하루씩 신청하세요.')
    days=[start+timedelta(days=i) for i in range((end-start).days+1)]
    holidays=set(LeaveHoliday.objects.filter(date__range=(start,end)).values_list('date',flat=True))
    holidays.update(PublicHoliday.objects.filter(date__range=(start,end),active=True).values_list('date',flat=True))
    days=[d.isoformat() for d in days if d.weekday()<5 and d not in holidays]
    if not days and not allow_empty:raise ValidationError('선택한 기간에 근무일이 없습니다.')
    return Decimal(len(days))*(Decimal('0.5') if kind!='annual' else Decimal('1')),days

def event(item,user,action,body=''):
    e=profile(user)
    LeaveEvent.objects.create(request=item,actor=user,actor_name=str(e) if e else user.get_username(),action=action,body=body)
    from .audit import write
    write('change','LeaveRequest',item.pk,'휴가 신청',after={'state':item.state,'revision':item.revision,'action':action},actor=user)

def steps(item,chain,round):
    for n,(role,e,delegated) in enumerate(chain):
        LeaveApproval.objects.create(request=item,round=round,order=n,role=role,approver=e,approver_name=str(e),delegated=delegated)

def current(item):
    round=item.steps.order_by('-round').values_list('round',flat=True).first() or 0
    return item.steps.filter(round=round,decision='').order_by('order').first() if item.state in ('pending','cancel_pending') else None

@transaction.atomic
def submit(user,start,end,kind,reason):
    e=profile(user)
    if not e:raise PermissionDenied
    # Lock a stable employee row to serialize overlapping submissions on PostgreSQL.
    Employee.objects.select_for_update().get(pk=e.pk)
    employment=StaffEmployment.objects.filter(employee=e).first()
    if not employment:raise ValidationError('관리자에게 입사일 등록을 요청하세요.')
    if start<employment.joined_on or (employment.ended_on and end>employment.ended_on):raise ValidationError('재직 기간 안에서 신청하세요.')
    if not LeaveYear.objects.filter(year=start.year,calendar_confirmed=True).exists():raise ValidationError('관리자가 해당 연도의 근무일·공휴일 설정을 확인한 후 신청할 수 있습니다.')
    from django.conf import settings
    if getattr(settings,'WORKHUB_HOLIDAY_SYNC_ENABLED',True) and not HolidaySync.objects.filter(year=start.year,last_success__gte=timezone.now()-timedelta(hours=48)).exists():raise ValidationError('공휴일 자료 연결 상태를 관리자에게 확인해 주세요. 최신 자료 확인 후 신청할 수 있습니다.')
    value,dates=amount(start,end,kind)
    if not reason.strip():raise ValidationError('신청 사유를 입력하세요.')
    for old in LeaveRequest.objects.filter(employee=e,state__in=['pending','approved','cancel_pending'],start__lte=end,end__gte=start):
        if set(dates)&set(old.dates) and (kind=='annual' or old.kind=='annual' or kind==old.kind):raise ValidationError('같은 날짜에 겹치는 휴가 신청이 있습니다.')
    chain=route(e)
    item=LeaveRequest.objects.create(employee=e,start=start,end=end,kind=kind,days=value,dates=dates,reason=reason.strip())
    steps(item,chain,0);event(item,user,'휴가 신청')
    return item

@transaction.atomic
def change(pk,user,action,reason,revision):
    item=LeaveRequest.objects.select_for_update().select_related('employee').get(pk=pk)
    if not user.is_active:raise PermissionDenied
    if item.revision!=revision:raise ValidationError('다른 처리가 반영되었습니다. 새로고침 후 다시 확인하세요.')
    e=profile(user);owner=e and e.pk==item.employee_id
    step=current(item)
    if action in ('approve','reject'):
        if not step or not e or step.approver_id!=e.pk or not e.is_active:raise PermissionDenied
        if action=='approve' and hasattr(item,'calendar_review') and item.calendar_review.pending:raise ValidationError('휴무일이 변경되어 일수 확인이 필요합니다. 관리자 확인 후 승인할 수 있습니다.')
        from django.conf import settings
        if action=='approve' and getattr(settings,'WORKHUB_HOLIDAY_SYNC_ENABLED',True) and not HolidaySync.objects.filter(year=item.start.year,last_success__gte=timezone.now()-timedelta(hours=48)).exists():raise ValidationError('공휴일 자료 연결 상태를 관리자에게 확인한 후 승인해 주세요.')
        if not reason.strip():raise ValidationError('결재 의견을 입력하세요. 잔여 초과 승인 시에도 의견을 남겨 주세요.')
    elif action=='withdraw':
        if not owner or item.state!='pending':raise PermissionDenied
    elif action=='cancel':
        if not owner or item.state!='approved':raise PermissionDenied
        if not reason.strip():raise ValidationError('취소 사유를 입력하세요.')
    else:raise ValidationError('처리 항목을 확인하세요.')
    # Optimistic write also prevents double charge on SQLite, which has no row locks.
    if LeaveRequest.objects.filter(pk=pk,revision=revision).update(revision=revision+1)!=1:raise ValidationError('이미 처리된 요청입니다.')
    item.revision+=1
    if action=='withdraw':item.state='withdrawn'
    elif action=='cancel':
        original=list(item.steps.filter(round=0))
        if any(not s.approver.is_active or not s.approver.user_id or not s.approver.user.is_active for s in original):raise ValidationError('기존 결재자가 비활성 상태입니다. 관리자에게 문의하세요.')
        next_round=(item.steps.order_by('-round').values_list('round',flat=True).first() or 0)+1
        steps(item,[(s.role,s.approver,s.delegated) for s in original],next_round);item.state='cancel_pending'
    else:
        cancelling=item.state=='cancel_pending'
        step.decision=action;step.decided_at=timezone.now();step.save()
        if action=='reject':item.state='approved' if cancelling else 'rejected'
        else:
            # Representative's one decision covers the vacant head slot and final role.
            if step.role=='head' and step.delegated:
                item.steps.filter(round=step.round,decision='',approver=e).update(decision='approve',decided_at=step.decided_at)
            if not item.steps.filter(round=step.round,decision='').exists():
                item.state='cancelled' if cancelling else 'approved'
                LeaveEntry.objects.create(employee=item.employee,year=item.start.year,amount=item.days if cancelling else -item.days,reason=('휴가 취소 복원' if cancelling else '휴가 승인 차감'),actor=user,key=f'leave:{item.pk}:{"restore" if cancelling else "charge"}')
                if not cancelling:item.approved_at=timezone.now()
    item.save(update_fields=['state','approved_at'])
    event(item,user,{'approve':'승인','reject':'반려','withdraw':'신청 회수','cancel':'취소 신청'}[action],reason.strip())
    return item
