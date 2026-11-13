"""Meetings share calendar/report storage, with an independent attendance workflow."""
import uuid
from datetime import timedelta
from django import forms
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.validators import validate_email
from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST
from .models import Meeting, MeetingAttendee, MeetingMail, MicrosoftConnection, Employee, Task, WorkCategory, TaskParticipation, WorkNotification
from .permissions import profile, visible_tasks, is_company_admin
from .workflow import record, snapshot
from . import microsoft_meetings as microsoft


def meeting_for(task): return getattr(task,'meeting',None)


def can_manage(user,item): return user.is_active and (item.organizer_id==user.pk or is_company_admin(user))


class MeetingForm(forms.Form):
    title=forms.CharField(label='미팅명',max_length=160)
    starts_at=forms.DateTimeField(label='미팅 일시 · 한국 시간',widget=forms.DateTimeInput(attrs={'type':'datetime-local'},format='%Y-%m-%dT%H:%M'))
    duration=forms.IntegerField(label='소요시간 · 분',min_value=5,max_value=1440,initial=60,widget=forms.NumberInput(attrs={'step':5,'list':'meeting-durations'}))
    attendees=forms.ModelMultipleChoiceField(label='참석자',queryset=Employee.objects.none(),required=False,widget=forms.CheckboxSelectMultiple,
        help_text='주최자는 자동으로 포함됩니다. 다른 팀 직원도 선택할 수 있습니다.')
    mode=forms.ChoiceField(label='진행 방식',choices=Meeting._meta.get_field('mode').choices)
    location=forms.CharField(label='장소 · 회의실',max_length=200,required=False)
    provider=forms.ChoiceField(label='온라인 접속',choices=Meeting._meta.get_field('provider').choices,required=False)
    join_url=forms.URLField(label='미팅 접속 링크',max_length=2000,required=False,assume_scheme='https')
    description=forms.CharField(label='목적 · 안건',max_length=10000,required=False,widget=forms.Textarea(attrs={'rows':3}))
    guests=forms.CharField(label='외부 참석자 이메일 · 선택',max_length=3000,required=False,widget=forms.Textarea(attrs={'rows':2}),help_text='쉼표 또는 줄바꿈으로 구분합니다. Teams 자동 생성 시 Microsoft 회의 초대를 보냅니다.')
    customer=forms.CharField(label='고객사 · 거래처',max_length=120,required=False)
    revision=forms.IntegerField(widget=forms.HiddenInput,initial=0)
    token=forms.UUIDField(widget=forms.HiddenInput)

    def __init__(self,*args,user,item=None,**kwargs):
        self.user=user;self.item=item
        super().__init__(*args,**kwargs)
        self.fields['attendees'].queryset=Employee.objects.filter(is_active=True,user__is_active=True).exclude(user__email='').select_related('department')
        if item and item.event_id:
            self.fields['provider'].disabled=True
            self.initial['provider']='teams'
    def clean_guests(self):
        values=list(dict.fromkeys(v.strip().lower() for v in self.cleaned_data['guests'].replace('\n',',').replace(';',',').split(',') if v.strip()))
        if len(values)>20:raise ValidationError('외부 참석자는 최대 20명까지 등록하세요.')
        for value in values:validate_email(value)
        return values
    def clean_join_url(self):
        value=self.cleaned_data.get('join_url','')
        if value:
            from urllib.parse import urlsplit
            parts=urlsplit(value)
            if parts.scheme!='https' or parts.username or parts.password:raise ValidationError('https로 시작하는 미팅 링크를 입력하세요.')
        return value
    def clean(self):
        data=super().clean();online=data.get('mode') in ['online','hybrid'];provider=data.get('provider') or 'link'
        if data.get('mode') in ['outside','hybrid'] and not data.get('location'):self.add_error('location','미팅 장소를 입력하세요.')
        if online and provider=='link' and not data.get('join_url'):self.add_error('join_url','온라인 미팅 접속 링크를 입력하세요.')
        organizer=self.item.organizer if self.item else self.user
        if online and provider=='teams' and not MicrosoftConnection.objects.filter(user=organizer).exists():self.add_error('provider','주최자의 Microsoft 계정을 먼저 연결해 주세요.')
        if self.item and self.item.event_id and not online:self.add_error('mode','Teams로 초대한 미팅은 온라인 또는 병행으로 유지하세요. 전환하려면 취소 후 새 미팅을 등록하세요.')
        if data.get('guests') and (not online or provider!='teams'):self.add_error('guests','외부 참석자 초대는 Teams 자동 생성에서 사용할 수 있습니다.')
        if not online:data.update(provider='link',join_url='')
        data['provider']=provider if online else 'link'
        if data.get('attendees') is not None:
            internal=set(data['attendees'].values_list('user__email',flat=True))|{organizer.email}
            data['guests']=[e for e in data.get('guests',[]) if e not in {x.lower() for x in internal}]
        return data


def queue(item,actor,label,removed=()):
    task=item.task
    targets={r.employee.user_id:r.employee.user for r in item.attendees.filter(active=True,employee__is_active=True,employee__user__is_active=True).select_related('employee__user')}
    targets[item.organizer_id]=item.organizer
    from .hiworks import enqueue
    for uid,user in targets.items():
        if uid==actor.pk:continue
        notification=WorkNotification.objects.create(user=user,task=task,message=(label+' · '+task.title)[:240]);enqueue(notification)
        if user.email:MeetingMail.objects.get_or_create(meeting=item,revision=item.revision,email=user.email.lower(),defaults={'user':user})
    for user in removed:
        if user.email:MeetingMail.objects.get_or_create(meeting=item,revision=item.revision,email=user.email.lower(),defaults={'user':user,'removed':True})


@transaction.atomic
def save(form,user,item=None):
    data=form.cleaned_data
    if item:
        item=Meeting.objects.select_for_update().select_related('task','organizer').get(pk=item.pk)
        if not can_manage(user,item) or item.task.status in ['cancelled','done'] or item.task.is_deleted:raise PermissionDenied
        if item.revision!=data['revision']:raise ValidationError('다른 변경이 반영되었습니다. 새로고침 후 다시 확인하세요.')
        if item.sync_state=='sending' or (item.sync_state=='unknown' and not item.event_id):raise ValidationError('Teams 처리 결과를 먼저 확인해 주세요. 중복 초대 방지를 위해 연결 확인 후 변경할 수 있습니다.')
        task=item.task;before=snapshot(task);item.revision+=1
    else:
        existing=Meeting.objects.filter(transaction_id=data['token']).first()
        if existing:
            if existing.organizer_id!=user.pk:raise PermissionDenied
            return existing
        owner=profile(user)
        if not owner:raise ValidationError('미팅 주최자의 직원 계정으로 로그인해 주세요.')
        task=Task(creator=user,assignee=owner,workflow_version=2,management_mode='event',assignment_email=False,assignment_messenger=False)
        item=Meeting(organizer=user,transaction_id=data['token'])
        before=None
    old=set(item.attendees.filter(active=True).values_list('employee_id',flat=True)) if item.pk else set()
    starts=data['starts_at'];ends=starts+timedelta(minutes=data['duration'])
    time_changed=bool(item.pk and (task.activity_start!=starts or task.activity_end!=ends))
    task.title=data['title'];task.description=data['description'];task.activity_start=starts;task.activity_end=ends
    task.start_date=timezone.localtime(starts).date();task.start_time=timezone.localtime(starts).time();task.due_date=timezone.localtime(ends).date()
    task.estimated_minutes=data['duration'];task.location=data['location'] or ('사내' if data['mode']=='office' else '')
    task.customer=data['customer'];task.category=WorkCategory.objects.get_or_create(name='미팅')[0];task.revision+=1;task.save()
    item.task=task;item.mode=data['mode'];item.provider=data['provider'];item.guests=data['guests']
    if item.provider!='teams':item.join_url=data['join_url']
    item.sync_state='pending' if item.provider=='teams' else 'local';item.sync_error='';item.save()
    selected=set(data['attendees'].values_list('pk',flat=True))|{task.assignee_id}
    removed=list(Employee.objects.filter(pk__in=old-selected,user__isnull=False).select_related('user'))
    item.attendees.exclude(employee_id__in=selected).update(active=False)
    TaskParticipation.objects.filter(task=task).exclude(employee_id__in=selected).delete()
    for eid in selected:
        attendee,new=MeetingAttendee.objects.get_or_create(meeting=item,employee_id=eid)
        if new or not attendee.active or time_changed:
            attendee.response='yes' if eid==task.assignee_id else 'pending';attendee.responded_at=timezone.now() if eid==task.assignee_id else None
        attendee.active=True;attendee.save()
        TaskParticipation.objects.get_or_create(task=task,employee_id=eid)
    record(task,user,'changed' if old else 'created','미팅 변경' if old else '미팅 등록',{'before':before} if before else None)
    queue(item,user,'미팅 변경' if old else '미팅 초대',[e.user for e in removed])
    return item


@login_required
@never_cache
def edit(request,pk=None):
    item=get_object_or_404(Meeting,pk=pk,task__in=visible_tasks(request.user)) if pk else None
    if item and not can_manage(request.user,item):raise PermissionDenied
    initial={'token':uuid.uuid4(),'starts_at':timezone.localtime().replace(second=0,microsecond=0),'mode':'office','provider':'link'}
    if request.GET.get('date'):
        try:initial['starts_at']=initial['starts_at'].replace(year=int(request.GET['date'][:4]),month=int(request.GET['date'][5:7]),day=int(request.GET['date'][8:10]))
        except (ValueError,TypeError):pass
    if item:
        initial.update(title=item.task.title,starts_at=timezone.localtime(item.task.activity_start),duration=item.task.estimated_minutes,
            mode=item.mode,provider=item.provider,join_url=item.join_url,location=item.task.location,description=item.task.description,
            customer=item.task.customer,attendees=list(item.attendees.filter(active=True).values_list('employee_id',flat=True)),
            guests=', '.join(item.guests),revision=item.revision,token=item.transaction_id)
    form=MeetingForm(request.POST if request.method=='POST' else None,user=request.user,item=item,initial=initial)
    if request.method=='POST' and form.is_valid():
        try:result=save(form,request.user,item)
        except ValidationError as exc:form.add_error(None,exc)
        else:
            messages.success(request,'미팅을 저장했습니다.'+(' Teams 연결 결과는 미팅 화면에서 확인하세요.' if result.provider=='teams' else ''))
            return redirect('meeting_detail',pk=result.pk)
    return render(request,'tasks/meeting_form.html',{'form':form,'meeting':item,'microsoft_connected':MicrosoftConnection.objects.filter(user=item.organizer if item else request.user).exists()})


@login_required
@never_cache
def detail(request,pk):
    item=get_object_or_404(Meeting.objects.select_related('task','organizer'),pk=pk,task__in=visible_tasks(request.user))
    attendee=item.attendees.filter(employee__user=request.user,active=True,employee__is_active=True).first()
    if request.method=='POST':
        with transaction.atomic():
            item=Meeting.objects.select_for_update().select_related('task','organizer').get(pk=pk)
            if str(item.revision)!=request.POST.get('revision'):messages.error(request,'다른 변경이 반영되었습니다. 새로고침 후 다시 확인하세요.');return redirect('meeting_detail',pk=pk)
            action=request.POST.get('action')
            if action=='respond':
                if not attendee or item.task.status in ['cancelled','done'] or request.POST.get('response') not in ['yes','maybe','no']:raise PermissionDenied
                if attendee.response==request.POST['response']:return redirect('meeting_detail',pk=pk)
                attendee.response=request.POST['response'];attendee.responded_at=timezone.now();attendee.save()
                record(item.task,request.user,'comment','미팅 참석 응답 · '+attendee.get_response_display())
                if request.user.pk!=item.organizer_id:
                    from .hiworks import enqueue
                    notice=WorkNotification.objects.create(user=item.organizer,task=item.task,message=str(attendee.employee)+' · '+attendee.get_response_display());enqueue(notice)
                messages.success(request,'참석 응답을 저장했습니다.')
                return redirect('meeting_detail',pk=pk)
            if not can_manage(request.user,item):raise PermissionDenied
            if action=='retry' and item.provider=='teams':
                if item.sync_state not in ['error','unknown']:raise PermissionDenied
                item.sync_state='pending';item.sync_error='';item.save(update_fields=['sync_state','sync_error'])
                messages.success(request,'Teams 연결을 다시 확인합니다.')
                return redirect('meeting_detail',pk=pk)
            if item.task.status=='cancelled' or (item.task.status=='done' and action!='cancel'):raise PermissionDenied
            if item.sync_state=='sending':
                messages.error(request,'Teams 연결 처리 중입니다. 잠시 후 다시 진행하세요.')
                return redirect('meeting_detail',pk=pk)
            if item.provider=='teams' and ((action=='finish' and item.sync_state!='synced') or (action=='cancel' and item.sync_state=='unknown' and not item.event_id)):
                messages.error(request,'Teams 처리 결과를 먼저 확인해 주세요. 연결 확인 후 종료·취소할 수 있습니다.')
                return redirect('meeting_detail',pk=pk)
            if action=='cancel':
                item.task.status='cancelled';label='미팅 취소'
            elif action=='finish':
                item.task.status='done';item.task.completed_at=timezone.now();item.task.progress=100
                item.result=request.POST.get('result','').strip()[:10000];label='미팅 종료 · 결과 기록'
            else:raise PermissionDenied
            item.revision+=1;item.task.revision+=1;item.task.save()
            if action=='cancel' and item.provider=='teams':item.sync_state='pending'
            item.save();record(item.task,request.user,'cancelled' if action=='cancel' else 'comment',label)
            queue(item,request.user,label);messages.success(request,label+'을 저장했습니다.')
        return redirect('meeting_detail',pk=pk)
    conflicts=visible_tasks(request.user).filter(activity_start__lt=item.task.activity_end,activity_end__gt=item.task.activity_start).exclude(pk=item.task_id).exclude(status__in=['cancelled','done','draft']).filter(Q(assignee__in=item.task.participants.all())|Q(participants__in=item.task.participants.all())).distinct()[:10]
    return render(request,'tasks/meeting_detail.html',{'meeting':item,'task':item.task,'can_manage':can_manage(request.user,item),
        'attendee':attendee,'attendees':item.attendees.filter(active=True).select_related('employee__department'),
        'conflicts':conflicts,'events':item.task.events.select_related('author__employee__department')})


def deliver_pending():
    from accounts.lifecycle import account_allowed
    from accounts.services import deliver
    from django.template.loader import render_to_string
    for mail in MeetingMail.objects.filter(state='pending').select_related('meeting__task','user')[:30]:
        item=mail.meeting;task=item.task
        if item.provider=='teams' and item.sync_state in ['pending','sending'] and task.status!='done':continue
        if not MeetingMail.objects.filter(pk=mail.pk,state='pending').update(state='sending'):continue
        if (item.revision!=mail.revision or not mail.user or not account_allowed(mail.user) or not mail.user.is_active
                or mail.user.email.lower()!=mail.email):state='skipped'
        else:
            title='미팅 참석 제외' if mail.removed else '미팅 취소' if task.status=='cancelled' else '미팅 종료' if task.status=='done' else '미팅 안내'
            url=settings.WORKHUB_BASE_URL.rstrip('/')+reverse('meeting_detail',args=[item.pk])
            try:deliver('[Workhub] '+title+' · '+task.title,title+' · '+task.title+'\n'+url,mail.email,
                html_body=render_to_string('tasks/email_meeting.html',{'meeting':item,'task':task,'title':title,'url':url,'removed':mail.removed}))
            except Exception:state='unknown'
            else:state='sent'
        MeetingMail.objects.filter(pk=mail.pk).update(state=state)


@transaction.atomic
def remind_upcoming(now=None):
    now=now or timezone.now()
    from accounts.lifecycle import account_allowed
    from .hiworks import enqueue
    for row in MeetingAttendee.objects.filter(active=True,meeting__task__is_deleted=False,
        meeting__task__activity_start__gt=now,meeting__task__activity_start__lte=now+timedelta(minutes=15),
        employee__is_active=True,employee__user__is_active=True).exclude(response='no').exclude(meeting__task__status__in=['cancelled','done']).select_related('meeting__task','employee__user'):
        if row.reminded_revision>=row.meeting.revision or not account_allowed(row.employee.user):continue
        if MeetingAttendee.objects.filter(pk=row.pk,reminded_revision=row.reminded_revision).update(reminded_revision=row.meeting.revision):
            notice=WorkNotification.objects.create(user=row.employee.user,task=row.meeting.task,message=('미팅 시작 15분 전 · '+row.meeting.task.title)[:240]);enqueue(notice)
