"""Korean-time calendar rules. Existing date-only recurrence remains compatible."""
import calendar
from datetime import datetime,timedelta
from zoneinfo import ZoneInfo
from django import forms
from django.db.models import Q
from django.utils import timezone
from .models import PersonalSchedule,PersonalReminder,PersonalCard,PersonalTag,CalendarExport

SEOUL=ZoneInfo('Asia/Seoul')
KINDS=[('','반복 안 함'),('daily','매일'),('weekdays','평일 · 월~금'),('weekly','매주 · 요일 선택'),('monthly_date','매월 · 날짜 기준'),('monthly_weekday','매월 · n번째 요일'),('yearly','매년')]
DEFAULT_TAGS=['일반 업무','유지보수','정기 점검','회의','자료 정리','아이디어','학습','기타']


def card_datetime(card):
    if not card.due_date: return None
    return datetime.combine(card.due_date,card.due_time or datetime.min.time(),tzinfo=SEOUL)


def next_run(rule,after):
    start=timezone.localtime(rule.starts_at,SEOUL)
    day=max(start.date(),timezone.localtime(after,SEOUL).date())
    weekdays=rule.weekdays or [start.weekday()]
    # Bounded horizon covers yearly/custom intervals and sparse leap-day schedules.
    for _ in range(366*40):
        candidate=datetime.combine(day,start.timetz(),tzinfo=SEOUL)
        if candidate>after and candidate>=start:
            delta=(day-start.date()).days
            months=(day.year-start.year)*12+day.month-start.month
            if rule.kind=='daily': match=delta%rule.interval==0
            elif rule.kind=='weekdays':
                week_start=start.date()-timedelta(days=start.weekday())
                match=day.weekday()<5 and ((day-week_start).days//7)%rule.interval==0
            elif rule.kind=='weekly':
                week_start=start.date()-timedelta(days=start.weekday())
                match=((day-week_start).days//7)%rule.interval==0 and day.weekday() in weekdays
            elif rule.kind=='monthly_weekday':
                last=calendar.monthrange(day.year,day.month)[1]
                ordinal=(day.day-1)//7+1
                match=months%rule.interval==0 and day.weekday()==weekdays[0] and (ordinal==rule.week_number if rule.week_number>0 else day.day+7>last)
            elif rule.kind in ['monthly_date','yearly']:
                last=calendar.monthrange(day.year,day.month)[1]
                target=last if rule.month_day==32 else rule.month_day
                if target>last: target=last if rule.month_end=='last' else -1
                match=day.day==target and (months%rule.interval==0 if rule.kind=='monthly_date' else day.month==start.month and (day.year-start.year)%rule.interval==0)
            else: return None
            if match:
                if rule.until and day>rule.until: return None
                return candidate
        day+=timedelta(days=1)
    return None


class ScheduleFields(forms.Form):
    recurrence=forms.ChoiceField(label='반복 규칙',choices=KINDS,required=False)
    schedule_mode=forms.ChoiceField(label='다음 회차 생성 기준',choices=[('calendar','정해진 일정마다 · 이전 회차 미완료여도 생성'),('completion','완료 후 다음 회차 생성')],required=False,initial='calendar')
    repeat_interval=forms.IntegerField(label='반복 간격',min_value=1,max_value=366,required=False,initial=1,help_text='예: 매주 + 2 = 2주마다')
    repeat_weekdays=forms.TypedMultipleChoiceField(label='반복 요일',choices=list(enumerate(['월','화','수','목','금','토','일'])),coerce=int,required=False,widget=forms.CheckboxSelectMultiple)
    repeat_month_day=forms.TypedChoiceField(label='매월 날짜',choices=[('','시작 날짜 기준')]+[(n,f'{n}일') for n in range(1,32)]+[(32,'매월 말일')],coerce=int,required=False)
    repeat_week_number=forms.TypedChoiceField(label='매월 몇 번째 요일',choices=[(1,'첫째'),(2,'둘째'),(3,'셋째'),(4,'넷째'),(-1,'마지막')],coerce=int,required=False,initial=1)
    missing_day=forms.ChoiceField(label='해당 날짜가 없는 달',choices=[('last','그 달 말일에 실행'),('skip','그 달 건너뛰기')],required=False,initial='last')
    repeat_until=forms.DateField(label='반복 종료일 (선택)',required=False,widget=forms.DateInput(attrs={'type':'date'}))
    repeat_count=forms.IntegerField(label='총 회차 수 (선택)',required=False,min_value=1,max_value=10000)


def save_schedule(card,data,changed):
    kind=data.get('recurrence')
    old=PersonalSchedule.objects.filter(template_card=card).first()
    if not kind:
        if old: old.enabled=False;old.next_at=None;old.save(update_fields=['enabled','next_at'])
        return
    if not changed and old: return
    start=card_datetime(card)
    values={'column':card.column if card.column.workflow_role!='done' else (card.column.board.columns.filter(workflow_role='todo').first() or card.column),
        'mode':data.get('schedule_mode') or 'calendar','kind':kind,'interval':data.get('repeat_interval') or 1,
        'weekdays':data.get('repeat_weekdays') or [start.weekday()], 'month_day':data.get('repeat_month_day') or start.day,
        'week_number':data.get('repeat_week_number') or 1,'month_end':data.get('missing_day') or 'last',
        'starts_at':start,'until':data.get('repeat_until'),'max_count':data.get('repeat_count'),
        'enabled':True,'generated_count':1,'next_at':None}
    rule,_=PersonalSchedule.objects.update_or_create(template_card=card,defaults=values)
    if old: rule.rule_version+=1
    rule.next_at=None if rule.max_count==1 else next_run(rule,start)
    rule.save()
    card.series=rule;card.occurrence_at=start;card.save(update_fields=['series','occurrence_at'])


def arrange_reminder(card):
    when=card_datetime(card)
    if card.state!='active' or not when or card.due_time is None or not card.reminder_enabled:
        card.reminders.filter(delivered_at__isnull=True).delete();return
    card.reminders.exclude(scheduled_for=when).filter(delivered_at__isnull=True).delete()
    reminder,_=PersonalReminder.objects.get_or_create(card=card,scheduled_for=when,defaults={'notify_at':when-timedelta(minutes=card.reminder_minutes)})
    if reminder.delivered_at is None:
        reminder.notify_at=when-timedelta(minutes=card.reminder_minutes);reminder.save(update_fields=['notify_at'])


def arrange_export(card):
    if card.calendar_display=='none':
        CalendarExport.objects.filter(card=card).update(detail='none')
    else:
        CalendarExport.objects.update_or_create(card=card,defaults={'user':card.column.board.owner,'detail':card.calendar_display})


def create_occurrence(rule,when):
    template=rule.template_card
    col=rule.column
    if col.workflow_role=='done': col=col.board.columns.filter(workflow_role='todo').first() or col
    local=timezone.localtime(when,SEOUL)
    card,created=PersonalCard.objects.get_or_create(series=rule,occurrence_at=when,defaults={
        'column':col,'title':template.title,'body':template.body,'priority':template.priority,'label':template.label,
        'checklist':[{'text':i['text'],'done':False} for i in template.checklist], 'due_date':local.date(),'due_time':local.time().replace(tzinfo=None) if template.due_time else None,
        'reminder_minutes':template.reminder_minutes,'reminder_enabled':template.reminder_enabled,'calendar_display':template.calendar_display,'repeat':''})
    if created:
        card.tags.set(template.tags.filter(Q(owner__isnull=True)|Q(owner=template.column.board.owner)))
        arrange_reminder(card);arrange_export(card)
    return card,created


def completion_followup(card,now=None):
    if not card.series_id or card.next_occurrence_id: return
    rule=PersonalSchedule.objects.select_for_update().get(pk=card.series_id)
    if not rule.enabled or rule.mode!='completion' or (rule.max_count and rule.generated_count>=rule.max_count): return
    finished=timezone.localtime(now or timezone.now(),SEOUL)
    day=finished.date()
    if rule.kind in ['daily','weekly']: day+=timedelta(days=rule.interval*(7 if rule.kind=='weekly' else 1))
    elif rule.kind in ['monthly_date','yearly']:
        months=rule.interval*(12 if rule.kind=='yearly' else 1)
        year,month=divmod(day.year*12+day.month-1+months,12)
        day=day.replace(year=year,month=month+1,day=min(day.day,calendar.monthrange(year,month+1)[1]))
    else: return
    when=datetime.combine(day,timezone.localtime(rule.starts_at,SEOUL).timetz(),tzinfo=SEOUL)
    if rule.until and day>rule.until: return
    nxt,created=create_occurrence(rule,when)
    card.next_occurrence=nxt
    if created: rule.generated_count+=1;rule.save(update_fields=['generated_count'])
