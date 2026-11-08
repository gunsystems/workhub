"""Owner-only personal boards. No superuser bypass and no official-work metrics."""
import calendar
from copy import deepcopy
from datetime import timedelta
from django import forms
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Max, Q
from django.http import JsonResponse,Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST
from django.views.decorators.cache import never_cache
from .models import PersonalBoard, PersonalColumn, PersonalCard,PersonalTag,PersonalSchedule
from .schedules import ScheduleFields,save_schedule,arrange_reminder,arrange_export,completion_followup,card_datetime


def identifier(value):
    if not str(value or '').isdigit(): raise Http404
    return int(value)


class CardForm(forms.ModelForm):
    items = forms.CharField(label='체크리스트', required=False, widget=forms.Textarea(attrs={'rows':4}),help_text='한 줄에 한 항목씩 입력하세요.')
    new_tag=forms.CharField(label='새 태그 이름',max_length=40,required=False,help_text='새 태그는 본인에게만 표시되며 다음 카드에서도 선택할 수 있습니다.')
    reminder_minutes=forms.TypedChoiceField(label='미리 알림',choices=[(0,'예정 시간에'),(5,'5분 전'),(10,'10분 전'),(30,'30분 전'),(60,'1시간 전'),(1440,'하루 전')],coerce=int,required=False,initial=0)
    class Meta:
        model=PersonalCard
        fields=['title','body','column','priority','tags','due_date','due_time','reminder_enabled','reminder_minutes','repeat','calendar_display']
        widgets={'body':forms.Textarea(attrs={'rows':6}),'due_date':forms.DateInput(attrs={'type':'date'}),
                 'due_time':forms.TimeInput(attrs={'type':'time'}),'tags':forms.CheckboxSelectMultiple,'repeat':forms.HiddenInput}
    def __init__(self,*args,owner=None,**kwargs):
        super().__init__(*args,**kwargs)
        self.fields['items'].label='체크리스트'
        self.fields['column'].label='목록'
        self.fields['column'].queryset=PersonalColumn.objects.filter(board__owner=owner).select_related('board')
        self.fields['column'].label_from_instance=lambda col:f'{col.board.name} / {col.name}'
        self.fields['priority'].required=False
        self.fields['tags'].label='태그'
        self.fields['tags'].queryset=PersonalTag.objects.filter(Q(owner__isnull=True)|Q(owner=owner))
        self.fields['calendar_display'].required=False
        self.fields.update(deepcopy(ScheduleFields.base_fields))
        self.owner=owner
        if self.instance.pk and self.instance.repeat and not self.instance.series_id:
            self.initial.update(recurrence={'daily':'daily','weekly':'weekly','monthly':'monthly_date'}[self.instance.repeat],schedule_mode='completion',repeat_interval=1,
                repeat_month_day=self.instance.repeat_day or (self.instance.due_date.day if self.instance.due_date else None))
        if self.instance.pk and self.instance.series_id:
            rule=self.instance.series
            self.initial.update(recurrence=rule.kind,schedule_mode=rule.mode,repeat_interval=rule.interval,
                repeat_weekdays=rule.weekdays,repeat_month_day=rule.month_day,repeat_week_number=rule.week_number,
                missing_day=rule.month_end,repeat_until=rule.until,repeat_count=rule.max_count)
            if rule.template_card_id!=self.instance.pk:
                for name in ScheduleFields.base_fields:self.fields[name].disabled=True
        if self.instance.pk: self.initial['items']='\n'.join(item['text'] for item in self.instance.checklist)
    def clean_items(self):
        lines=[line.strip() for line in self.cleaned_data['items'].splitlines() if line.strip()]
        if len(lines)>100 or any(len(line)>300 for line in lines): raise ValidationError('체크리스트는 항목당 300자, 최대 100개까지 입력하세요.')
        return lines
    def clean_priority(self): return self.cleaned_data.get('priority') or 'normal'
    def clean_calendar_display(self): return self.cleaned_data.get('calendar_display') or 'none'
    def clean_reminder_minutes(self): return self.cleaned_data.get('reminder_minutes') or 0
    def clean_new_tag(self): return self.cleaned_data.get('new_tag','').strip()
    def clean(self):
        data=super().clean()
        if data.get('repeat') and not data.get('due_date'): self.add_error('due_date','반복을 시작할 기한을 지정하세요.')
        kind=data.get('recurrence')
        if (data.get('due_time') or kind or data.get('calendar_display')!='none') and not data.get('due_date'):
            self.add_error('due_date','날짜를 지정하세요.')
        if kind and data.get('schedule_mode')!='completion' and not data.get('due_time'):
            self.add_error('due_time','일정 기준 반복에는 시간을 지정하세요.')
        if kind=='weekly' and data.get('schedule_mode')!='completion' and not data.get('repeat_weekdays'):
            self.add_error('repeat_weekdays','반복할 요일을 선택하세요.')
        if kind=='monthly_weekday' and len(data.get('repeat_weekdays',[]))!=1:
            self.add_error('repeat_weekdays','매월 n번째 요일은 요일 하나를 선택하세요.')
        if data.get('schedule_mode')=='completion' and kind in ['weekdays','monthly_weekday']:
            self.add_error('recurrence','이 규칙은 일정 기준 반복으로 설정하세요.')
        if data.get('repeat_until') and data.get('due_date') and data['repeat_until']<data['due_date']:
            self.add_error('repeat_until','종료일은 시작일보다 빠를 수 없습니다.')
        if kind and (data.get('repeat_interval') or 1)>{'daily':366,'weekly':52,'weekdays':52,'monthly_date':120,'monthly_weekday':120,'yearly':10}[kind]:
            self.add_error('repeat_interval','반복 간격이 너무 큽니다. 일 단위 366, 주 단위 52, 월 단위 120, 연 단위 10까지 설정할 수 있습니다.')
        if kind and data.get('schedule_mode')!='completion' and data.get('due_date') and data.get('due_time'):
            from datetime import datetime,timedelta
            from .schedules import SEOUL,next_run
            start=datetime.combine(data['due_date'],data['due_time'],tzinfo=SEOUL)
            probe=PersonalSchedule(starts_at=start,kind=kind,interval=data.get('repeat_interval') or 1,
                weekdays=data.get('repeat_weekdays') or [start.weekday()],month_day=data.get('repeat_month_day') or start.day,
                week_number=data.get('repeat_week_number') or 1,month_end=data.get('missing_day') or 'last',until=data.get('repeat_until'))
            if next_run(probe,start-timedelta(seconds=1))!=start:
                self.add_error('due_date','시작 날짜를 선택한 반복 요일·날짜에 맞춰 주세요. 이 날짜가 첫 회차가 됩니다.')
        if self.instance.pk and self.instance.series_id and self.instance.series.template_card_id==self.instance.pk and data.get('due_date'):
            from datetime import datetime
            from .schedules import SEOUL
            when=datetime.combine(data['due_date'],data.get('due_time') or datetime.min.time(),tzinfo=SEOUL)
            if PersonalCard.objects.filter(series=self.instance.series,occurrence_at=when).exclude(pk=self.instance.pk).exists():
                self.add_error('due_date','이 시간의 회차가 이미 있습니다. 해당 회차를 열어 수정하세요.')
        return data


def next_date(day, repeat, anchor=None):
    if repeat=='daily': return day+timedelta(days=1)
    if repeat=='weekly': return day+timedelta(days=7)
    year,month=day.year+(day.month==12), day.month%12+1
    return day.replace(year=year,month=month,day=min(anchor or day.day,calendar.monthrange(year,month)[1]))


class ColumnForm(forms.ModelForm):
    action=forms.CharField(widget=forms.HiddenInput,initial='configure_column')
    board=forms.IntegerField(widget=forms.HiddenInput)
    column=forms.IntegerField(widget=forms.HiddenInput)
    class Meta:
        model=PersonalColumn
        fields=['name','color','workflow_role']


def complete_card(card, source):
    if card.state!='active': return
    card.state='done'; card.completed_at=timezone.now()
    if card.series_id:
        completion_followup(card)
        return
    if card.repeat and card.due_date and not card.next_occurrence_id:
        target_board=card.column.board
        column=source if source.workflow_role!='done' and source.board_id==target_board.pk else (target_board.columns.filter(workflow_role='todo').first() or target_board.columns.exclude(workflow_role='done').first() or card.column)
        anchor=card.repeat_day or card.due_date.day
        card.next_occurrence=PersonalCard.objects.create(column=column,title=card.title,body=card.body,label=card.label,priority=card.priority,
            checklist=[{'text':i['text'],'done':False} for i in card.checklist],due_date=next_date(card.due_date,card.repeat,anchor),
            repeat=card.repeat,repeat_day=anchor,position=(column.cards.aggregate(n=Max('position'))['n'] or 0)+1)


def sync_column(card, source):
    if card.state not in ['active','done']: return
    if card.column.workflow_role=='done': complete_card(card,source)
    elif card.state=='done': card.state='active'; card.completed_at=None


@login_required
@never_cache
def board(request,pk=None):
    boards=PersonalBoard.objects.filter(owner=request.user)
    if pk is None:
        first=boards.first()
        if not first:
            return render(request,'tasks/personal_board_v2.html',{'boards':boards,'board':None})
        return redirect('personal_board',pk=first.pk)
    obj=get_object_or_404(boards,pk=pk)
    state=request.GET.get('state','board')
    if state not in ['board','active','done','archived','trash']: state='board'
    query=request.GET.get('q','').strip()[:200]
    columns=list(obj.columns.all())
    for col in columns:
        cards=(col.cards.filter(state__in=['active','done']) if state=='board' else col.cards.filter(state=state)).select_related('series').prefetch_related('tags')
        if query: cards=cards.filter(Q(title__icontains=query)|Q(body__icontains=query)|Q(label__icontains=query)|Q(tags__name__icontains=query)).distinct()
        col.visible_cards=list(cards)
    return render(request,'tasks/personal_board_v2.html',{'boards':boards,'board':obj,'columns':columns,'state':state,'query':query,'today':timezone.localdate(),
        'column_colors':PersonalColumn.COLORS,'column_roles':PersonalColumn.ROLES})


@login_required
@require_POST
@transaction.atomic
def board_action(request):
    action=request.POST.get('action')
    if action=='create':
        name=request.POST.get('name','').strip()[:80]
        if not name: return redirect('personal_home')
        obj=PersonalBoard.objects.create(owner=request.user,name=name)
        for n,(name,color,role) in enumerate([('할 일','slate','todo'),('진행 중','blue','doing'),('완료','green','done')]):
            PersonalColumn.objects.create(board=obj,name=name,position=n,color=color,workflow_role=role)
    else:
        obj=get_object_or_404(PersonalBoard.objects.select_for_update(),pk=identifier(request.POST.get('board')),owner=request.user)
        name=request.POST.get('name','').strip()[:80]
        if action=='column' and name:
            pos=(obj.columns.aggregate(n=Max('position'))['n'] or 0)+1
            PersonalColumn.objects.create(board=obj,name=name,position=pos)
        elif action=='rename' and name: obj.name=name; obj.save(update_fields=['name'])
        elif action=='rename_column' and name:
            col=get_object_or_404(obj.columns,pk=identifier(request.POST.get('column'))); col.name=name; col.save(update_fields=['name'])
        elif action=='configure_column':
            col=get_object_or_404(obj.columns,pk=identifier(request.POST.get('column')))
            form=ColumnForm(request.POST,instance=col)
            if not form.is_valid(): return render(request,'tasks/operation_form.html',{'form':form,'title':'목록 설정'})
            form.save()
    return redirect('personal_board',pk=obj.pk)


@login_required
@never_cache
@transaction.atomic
def edit_card(request,pk=None):
    card=get_object_or_404(PersonalCard.objects.select_for_update(),pk=pk,column__board__owner=request.user) if pk else None
    initial={}
    if not card and request.GET.get('column'):
        initial['column']=get_object_or_404(PersonalColumn,pk=identifier(request.GET['column']),board__owner=request.user)
    old_due=card.due_date if card else None
    source=card.column if card else None
    form=CardForm(request.POST if request.method=='POST' else None,instance=card,owner=request.user,initial=initial)
    if request.method=='POST' and form.is_valid():
        obj=form.save(commit=False)
        if obj.due_date and (obj.due_date!=old_due or not obj.repeat_day): obj.repeat_day=obj.due_date.day
        old={item['text']:item['done'] for item in obj.checklist}
        obj.checklist=[{'text':line,'done':old.get(line,False)} for line in form.cleaned_data['items']]
        if not obj.pk: obj.position=(obj.column.cards.aggregate(n=Max('position'))['n'] or 0)+1
        if not obj.pk or (source and source.pk!=obj.column_id): sync_column(obj,source or obj.column)
        obj.save()
        obj.tags.set(form.cleaned_data['tags'])
        name=form.cleaned_data.get('new_tag')
        if name:
            tag=PersonalTag.objects.filter(Q(owner__isnull=True)|Q(owner=request.user),name__iexact=name).first()
            if not tag: tag,_=PersonalTag.objects.get_or_create(owner=request.user,name=name)
            obj.tags.add(tag)
        if not obj.series_id or obj.series.template_card_id==obj.pk:
            keys=set(ScheduleFields.base_fields)|{'due_date','due_time'}
            if form.cleaned_data.get('recurrence'): obj.repeat='';obj.save(update_fields=['repeat'])
            save_schedule(obj,form.cleaned_data,not card or bool(keys.intersection(form.changed_data)))
            if obj.state=='done' and obj.series_id:
                completion_followup(obj);obj.save(update_fields=['next_occurrence'])
        arrange_reminder(obj);arrange_export(obj)
        return redirect('personal_board',pk=obj.column.board_id)
    from .models import GoogleCalendarConnection
    return render(request,'tasks/personal_card_form.html',{'form':form,'card':card,'title':'개인 카드 수정' if card else '개인 카드 추가',
        'google_connection':GoogleCalendarConnection.objects.filter(user=request.user).first()})


@login_required
@require_POST
@transaction.atomic
def card_action(request,pk):
    card=get_object_or_404(PersonalCard.objects.select_for_update(),pk=pk,column__board__owner=request.user)
    action=request.POST.get('action')
    source=card.column
    if action=='move':
        target=get_object_or_404(PersonalColumn,pk=identifier(request.POST.get('column')),board__owner=request.user)
        card.column=target
        card.save(update_fields=['column'])
        rows=list(target.cards.exclude(pk=pk))
        before=request.POST.get('before','')
        if before:
            anchor=get_object_or_404(target.cards,pk=identifier(before))
            index=next((n for n,item in enumerate(rows) if item.pk==anchor.pk),len(rows))
        else: index=len(rows)
        rows.insert(index,card)
        for n,item in enumerate(rows): item.position=n
        PersonalCard.objects.bulk_update(rows,['position'])
        sync_column(card,source)
    elif action=='complete' and card.state=='active':
        complete_card(card,source)
        target=card.column.board.columns.filter(workflow_role='done').first()
        if target: card.column=target; card.position=(target.cards.aggregate(n=Max('position'))['n'] or 0)+1
    elif action in ['archive','trash','restore']:
        card.state={'archive':'archived','trash':'trash','restore':'active'}[action]
        if action=='restore':
            card.completed_at=None
            if card.column.workflow_role=='done': card.column=card.column.board.columns.exclude(workflow_role='done').first() or card.column
    elif action=='check' and card.state=='active':
        try: index=int(request.POST.get('index','-1'))
        except ValueError: index=-1
        if 0<=index<len(card.checklist): card.checklist[index]['done']=not card.checklist[index]['done']
    else:
        return JsonResponse({'error':'사용할 수 없는 처리입니다.'},status=400) if request.headers.get('X-Requested-With')=='fetch' else redirect('personal_board',pk=card.column.board_id)
    card.save()
    if action in ['archive','trash']:PersonalSchedule.objects.filter(template_card=card).update(enabled=False,next_at=None)
    arrange_reminder(card);arrange_export(card)
    if request.headers.get('X-Requested-With')=='fetch': return JsonResponse({'ok':True,'completed':card.state=='done','repeated':bool(card.next_occurrence_id)})
    if request.POST.get('next')=='card': return redirect('personal_card_edit',pk=card.pk)
    return redirect('personal_board',pk=card.column.board_id)


@login_required
@never_cache
def promote(request,pk):
    card=get_object_or_404(PersonalCard,pk=pk,column__board__owner=request.user)
    # Reviewed draft: private data is never copied into official work automatically.
    from .collaboration import OfficialTaskForm, edit_task
    if request.method=='POST': return edit_task(request)
    initial={'title':card.title,'description':card.body,'due_date':card.due_date,'start_date':timezone.localdate(),
             'assignee':getattr(getattr(request.user,'employee',None),'pk',None),'status':'draft','allocation':'shared','revision':0}
    return render(request,'tasks/official_form.html',{'form':OfficialTaskForm(user=request.user,initial=initial),
        'promotion':True,'task':None})
