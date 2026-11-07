"""Owner-only personal boards. No superuser bypass and no official-work metrics."""
import calendar
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
from .models import PersonalBoard, PersonalColumn, PersonalCard


def identifier(value):
    if not str(value or '').isdigit(): raise Http404
    return int(value)


class CardForm(forms.ModelForm):
    items = forms.CharField(label='체크리스트', required=False, widget=forms.Textarea(attrs={'rows':4}),help_text='한 줄에 한 항목씩 입력하세요.')
    class Meta:
        model=PersonalCard
        fields=['title','body','column','label','due_date','repeat']
        widgets={'body':forms.Textarea(attrs={'rows':6}),'due_date':forms.DateInput(attrs={'type':'date'})}
    def __init__(self,*args,owner=None,**kwargs):
        super().__init__(*args,**kwargs)
        self.fields['items'].label='체크리스트'
        self.fields['column'].label='목록'
        self.fields['column'].queryset=PersonalColumn.objects.filter(board__owner=owner)
        if self.instance.pk: self.initial['items']='\n'.join(item['text'] for item in self.instance.checklist)
    def clean_items(self):
        lines=[line.strip() for line in self.cleaned_data['items'].splitlines() if line.strip()]
        if len(lines)>100 or any(len(line)>300 for line in lines): raise ValidationError('체크리스트는 항목당 300자, 최대 100개까지 입력하세요.')
        return lines
    def clean(self):
        data=super().clean()
        if data.get('repeat') and not data.get('due_date'): self.add_error('due_date','반복을 시작할 기한을 지정하세요.')
        return data


def next_date(day, repeat, anchor=None):
    if repeat=='daily': return day+timedelta(days=1)
    if repeat=='weekly': return day+timedelta(days=7)
    year,month=day.year+(day.month==12), day.month%12+1
    return day.replace(year=year,month=month,day=min(anchor or day.day,calendar.monthrange(year,month)[1]))


@login_required
@never_cache
def board(request,pk=None):
    boards=PersonalBoard.objects.filter(owner=request.user)
    if pk is None:
        first=boards.first()
        if not first:
            return render(request,'tasks/personal_board.html',{'boards':boards,'board':None})
        return redirect('personal_board',pk=first.pk)
    obj=get_object_or_404(boards,pk=pk)
    state=request.GET.get('state','active')
    if state not in ['active','done','archived','trash']: state='active'
    query=request.GET.get('q','').strip()[:200]
    columns=list(obj.columns.all())
    for col in columns:
        cards=col.cards.filter(state=state)
        if query: cards=cards.filter(Q(title__icontains=query)|Q(body__icontains=query)|Q(label__icontains=query))
        col.visible_cards=list(cards)
    return render(request,'tasks/personal_board.html',{'boards':boards,'board':obj,'columns':columns,'state':state,'query':query,'today':timezone.localdate()})


@login_required
@require_POST
@transaction.atomic
def board_action(request):
    action=request.POST.get('action')
    if action=='create':
        name=request.POST.get('name','').strip()[:80]
        if not name: return redirect('personal_home')
        obj=PersonalBoard.objects.create(owner=request.user,name=name)
        for n,name in enumerate(['할 일','진행 중','완료']): PersonalColumn.objects.create(board=obj,name=name,position=n)
    else:
        obj=get_object_or_404(PersonalBoard.objects.select_for_update(),pk=identifier(request.POST.get('board')),owner=request.user)
        name=request.POST.get('name','').strip()[:80]
        if action=='column' and name:
            pos=(obj.columns.aggregate(n=Max('position'))['n'] or 0)+1
            PersonalColumn.objects.create(board=obj,name=name,position=pos)
        elif action=='rename' and name: obj.name=name; obj.save(update_fields=['name'])
        elif action=='rename_column' and name:
            col=get_object_or_404(obj.columns,pk=identifier(request.POST.get('column'))); col.name=name; col.save(update_fields=['name'])
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
    form=CardForm(request.POST if request.method=='POST' else None,instance=card,owner=request.user,initial=initial)
    if request.method=='POST' and form.is_valid():
        obj=form.save(commit=False)
        if obj.due_date and (obj.due_date!=old_due or not obj.repeat_day): obj.repeat_day=obj.due_date.day
        old={item['text']:item['done'] for item in obj.checklist}
        obj.checklist=[{'text':line,'done':old.get(line,False)} for line in form.cleaned_data['items']]
        if not obj.pk: obj.position=(obj.column.cards.aggregate(n=Max('position'))['n'] or 0)+1
        obj.save()
        return redirect('personal_board',pk=obj.column.board_id)
    return render(request,'tasks/operation_form.html',{'form':form,'title':'개인 카드 수정' if card else '개인 카드 추가','hint':'나만 보기 · 공식 업무 보고·결재·평가에는 포함되지 않습니다.'})


@login_required
@require_POST
@transaction.atomic
def card_action(request,pk):
    card=get_object_or_404(PersonalCard.objects.select_for_update(),pk=pk,column__board__owner=request.user)
    action=request.POST.get('action')
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
    elif action=='complete' and card.state=='active':
        card.state='done'; card.completed_at=timezone.now()
        if card.repeat and card.due_date and not card.next_occurrence_id:
            # One follow-up per occurrence, created in the same locked transaction.
            anchor=card.repeat_day or card.due_date.day
            due=next_date(card.due_date,card.repeat,anchor)
            nxt=PersonalCard.objects.create(column=card.column,title=card.title,body=card.body,label=card.label,
                checklist=[{'text':i['text'],'done':False} for i in card.checklist],due_date=due,repeat=card.repeat,repeat_day=anchor,position=card.position+1)
            card.next_occurrence=nxt
    elif action in ['archive','trash','restore']:
        card.state={'archive':'archived','trash':'trash','restore':'active'}[action]
        if action=='restore': card.completed_at=None
    elif action=='check' and card.state=='active':
        try: index=int(request.POST.get('index','-1'))
        except ValueError: index=-1
        if 0<=index<len(card.checklist): card.checklist[index]['done']=not card.checklist[index]['done']
    else:
        return JsonResponse({'error':'사용할 수 없는 처리입니다.'},status=400) if request.headers.get('X-Requested-With')=='fetch' else redirect('personal_board',pk=card.column.board_id)
    card.save()
    if request.headers.get('X-Requested-With')=='fetch': return JsonResponse({'ok':True})
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
