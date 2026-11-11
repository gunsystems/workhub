from datetime import timedelta
from django import forms
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction,OperationalError
from django.forms import inlineformset_factory
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from .models import Survey,SurveyQuestion,SurveyParticipation,SurveySubmission,SurveyDelivery,Employee,HiworksLink
from .permissions import is_company_admin
from .audit import write


class SurveyForm(forms.ModelForm):
    class Meta:
        model=Survey
        fields=['title','description','anonymous','audience','departments','employees','starts_at','closes_at','send_email','send_messenger','reminders']
        labels={'departments':'대상 부서','employees':'대상 직원'}
        widgets={'description':forms.Textarea(attrs={'rows':3}),
                 'departments':forms.CheckboxSelectMultiple,'employees':forms.CheckboxSelectMultiple,
                 'starts_at':forms.DateTimeInput(format='%Y-%m-%dT%H:%M',attrs={'type':'datetime-local'}),
                 'closes_at':forms.DateTimeInput(format='%Y-%m-%dT%H:%M',attrs={'type':'datetime-local'})}
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        self.fields['employees'].queryset=Employee.objects.filter(is_active=True,user__is_active=True,user__workhub_access__verified_at__isnull=False).select_related('department')
    def clean(self):
        d=super().clean()
        if d.get('closes_at') and d.get('starts_at') and d['closes_at']<=d['starts_at']:self.add_error('closes_at','시작 시각 이후로 지정하세요.')
        if d.get('closes_at') and d['closes_at']<=timezone.now():self.add_error('closes_at','현재 이후로 지정하세요.')
        for kind,field in [('team','departments'),('person','employees')]:
            if d.get('audience')==kind and not d.get(field):self.add_error(field,'대상을 선택하세요.')
        return d


class QuestionForm(forms.ModelForm):
    class Meta:
        model=SurveyQuestion
        fields=['label','kind','options','required']
        widgets={'options':forms.Textarea(attrs={'rows':3})}
    def clean(self):
        d=super().clean()
        if d.get('kind') in ['single','multiple'] and d.get('label'):
            options=[x.strip() for x in d.get('options','').splitlines() if x.strip()]
            if not 2<=len(options)<=20 or len(set(options))!=len(options):self.add_error('options','중복 없이 2~20개의 선택지를 입력하세요.')
            if any(len(x)>150 for x in options):self.add_error('options','선택지는 150자 이내로 입력하세요.')
            d['options']='\n'.join(options)
        return d


QuestionSet=inlineformset_factory(Survey,SurveyQuestion,form=QuestionForm,extra=3,min_num=1,validate_min=True,max_num=20,validate_max=True,can_delete=True)


def survey_targets(survey):
    qs=Employee.objects.filter(is_active=True,user__is_active=True,user__workhub_access__verified_at__isnull=False)
    if survey.audience=='team':qs=qs.filter(department__in=survey.departments.all())
    elif survey.audience=='person':qs=qs.filter(pk__in=survey.employees.all())
    return qs.distinct()


@transaction.atomic
def publish(survey_id,actor):
    if not is_company_admin(actor):raise PermissionDenied
    survey=Survey.objects.select_for_update().get(pk=survey_id)
    if survey.state!='draft':raise ValidationError('이미 배포한 설문입니다.')
    if survey.closes_at<=timezone.now() or not survey.questions.exists():raise ValidationError('질문과 마감 시각을 확인하세요.')
    targets=list(survey_targets(survey))
    if not targets:raise ValidationError('인증을 마친 대상 직원이 없습니다.')
    SurveyParticipation.objects.bulk_create([SurveyParticipation(survey=survey,employee=e) for e in targets])
    survey.state='open';survey.save(update_fields=['state'])
    write('survey_publish','Survey',survey.pk,survey.title,after={'대상':len(targets),'익명':survey.anonymous},actor=actor)


def answer_form(survey,data=None,initial=None):
    form=forms.Form(data=data,initial=initial)
    for q in survey.questions.all():
        kw={'label':q.label,'required':q.required}
        options=[(x,x) for x in q.options.splitlines() if x]
        if q.kind=='text':field=forms.CharField(max_length=4000,widget=forms.Textarea(attrs={'rows':4}),**kw)
        elif q.kind=='multiple':field=forms.MultipleChoiceField(choices=options,widget=forms.CheckboxSelectMultiple,**kw)
        elif q.kind=='rating':field=forms.ChoiceField(choices=[('','선택하세요')]+[(str(n),str(n)) for n in range(1,6)],**kw)
        else:field=forms.ChoiceField(choices=options,widget=forms.RadioSelect,**kw)
        form.fields[str(q.pk)]=field
    return form


@transaction.atomic
def submit(survey_id,user,data,draft=False):
    survey=Survey.objects.select_for_update().get(pk=survey_id)
    part=get_object_or_404(SurveyParticipation.objects.select_for_update(),survey=survey,employee__user=user,employee__is_active=True)
    now=timezone.now()
    if survey.state!='open' or not survey.starts_at<=now<survey.closes_at:raise ValidationError('현재 응답 기간이 아닙니다.')
    if part.submitted:raise ValidationError('이미 제출했습니다. 제출한 응답은 변경되지 않습니다.')
    form=answer_form(survey,data)
    if draft:
        for field in form.fields.values():field.required=False
    if not form.is_valid():return form
    if draft:
        part.draft=form.cleaned_data;part.save(update_fields=['draft'])
    else:
        SurveySubmission.objects.create(survey=survey,employee=None if survey.anonymous else part.employee,answers=form.cleaned_data)
        part.submitted=True;part.draft={};part.save(update_fields=['submitted','draft'])
        # Do not audit answer contents or an anonymous answer/employee relationship.
    return None


@login_required
def survey_list(request):
    admin=is_company_admin(request.user)
    qs=Survey.objects.all() if admin else Survey.objects.filter(participations__employee__user=request.user).exclude(state='draft')
    return render(request,'tasks/survey_list.html',{'surveys':qs.distinct(),'survey_admin':admin})


@login_required
def edit(request,pk=None):
    if not is_company_admin(request.user):raise PermissionDenied
    obj=get_object_or_404(Survey,pk=pk,state='draft') if pk else Survey(creator=request.user,starts_at=timezone.localtime().replace(second=0,microsecond=0),closes_at=timezone.localtime()+timedelta(days=7))
    form=SurveyForm(request.POST if request.method=='POST' else None,instance=obj)
    questions=QuestionSet(request.POST if request.method=='POST' else None,instance=obj,prefix='questions')
    if request.method=='POST' and form.is_valid() and questions.is_valid():
        with transaction.atomic():
            if pk and Survey.objects.select_for_update().get(pk=pk).state!='draft':raise PermissionDenied
            obj=form.save();questions.instance=obj;questions.save()
            for index,q in enumerate(obj.questions.all()):q.order=index;q.save(update_fields=['order'])
        return redirect('survey_detail',pk=obj.pk)
    return render(request,'tasks/survey_form.html',{'form':form,'questions':questions,'survey':obj})


@login_required
def detail(request,pk):
    admin=is_company_admin(request.user)
    obj=get_object_or_404(Survey,pk=pk)
    part=SurveyParticipation.objects.filter(survey=obj,employee__user=request.user,employee__is_active=True).first()
    if not admin and (not part or obj.state=='draft'):raise PermissionDenied
    error='';form=None
    if request.method=='POST':
        try:
            action=request.POST.get('action')
            if action=='publish':publish(pk,request.user)
            elif action in ['close','extend']:
                if not admin:raise PermissionDenied
                with transaction.atomic():
                    obj=Survey.objects.select_for_update().get(pk=pk)
                    if obj.state=='draft':raise ValidationError('먼저 설문을 배포하세요.')
                    if action=='close':obj.state='closed'
                    else:
                        end=forms.DateTimeField().clean(request.POST.get('closes_at'))
                        if end<=max(timezone.now(),obj.closes_at):raise ValidationError('기존 마감과 현재 시각 이후로 연장하세요.')
                        obj.closes_at=end;obj.state='open'
                    obj.save(update_fields=['state','closes_at'])
                    write('survey_'+action,'Survey',pk,obj.title,after={'마감':obj.closes_at.isoformat()},actor=request.user)
            elif action in ['submit','draft']:form=submit(pk,request.user,request.POST,draft=action=='draft')
            else:raise ValidationError('처리 항목을 확인하세요.')
            if form is None:return redirect('survey_detail',pk=pk)
        except ValidationError as exc:error=exc.messages[0]
        except OperationalError:error='다른 처리가 진행 중입니다. 새로고침하여 제출 상태를 확인하세요.'
    obj.refresh_from_db()
    if part:part.refresh_from_db()
    if part and not part.submitted and form is None:form=answer_form(obj,initial=part.draft)
    parts=obj.participations.select_related('employee__department') if admin else []
    total=obj.participations.count() if admin else 0
    submitted=obj.participations.filter(submitted=True).count() if admin else 0
    active=obj.state=='open' and obj.starts_at<=timezone.now()<obj.closes_at
    return render(request,'tasks/survey_detail.html',{'survey':obj,'survey_admin':admin,'participation':part,'form':form,'error':error,'parts':parts,'total':total,'submitted':submitted,'active':active})


@login_required
def results(request,pk):
    if not is_company_admin(request.user):raise PermissionDenied
    obj=get_object_or_404(Survey,pk=pk)
    subs=list(obj.submissions.select_related('employee__department'))
    import secrets
    secrets.SystemRandom().shuffle(subs)
    rows=[]
    for q in obj.questions.all():
        vals=[s.answers.get(str(q.pk)) for s in subs]
        if q.kind in ['single','multiple','rating']:
            options=q.options.splitlines() if q.kind!='rating' else list(map(str,range(1,6)))
            counts=[{'label':o,'count':sum(o in (v if isinstance(v,list) else [v]) for v in vals)} for o in options]
        else:counts=[]
        rows.append({'question':q,'counts':counts,'texts':[v for v in vals if v] if q.kind=='text' else []})
    named=[{'employee':s.employee,'answers':[{'question':q.label,'value':s.answers.get(str(q.pk))} for q in obj.questions.all()]} for s in subs] if not obj.anonymous else []
    return render(request,'tasks/survey_results.html',{'survey':obj,'rows':rows,'named':named,'count':len(subs)})


def queue_notifications(now=None):
    now=now or timezone.now()
    Survey.objects.filter(state='open',closes_at__lte=now).update(state='closed')
    for survey in Survey.objects.filter(state='open',starts_at__lte=now,closes_at__gt=now):
        for part in survey.participations.filter(submitted=False,employee__is_active=True):
            stages=['initial']
            initial=part.deliveries.filter(stage='initial').first()
            if initial and survey.reminders:
                latest=part.deliveries.exclude(attempted_at=None).order_by('-attempted_at').first()
                if not latest or now-latest.attempted_at<timedelta(days=1):continue
                left=survey.closes_at-now
                # On first scheduling only send the most relevant reminder, never a burst.
                if timedelta(hours=0)<left<=timedelta(days=1):stages=['day1']
                elif left<=timedelta(days=3):stages=['day3']
                else:continue
            elif initial:continue
            for channel,enabled in [('email',survey.send_email),('messenger',survey.send_messenger)]:
                if enabled:SurveyDelivery.objects.get_or_create(participation=part,channel=channel,stage=stages[0])


def deliver_notifications(now=None):
    from accounts.hiworks_access import fresh_cache
    from accounts.lifecycle import account_allowed
    from accounts.services import deliver
    from .hiworks import api
    from django.template.loader import render_to_string
    now=now or timezone.now()
    if settings.WORKHUB_REQUIRE_HIWORKS and not fresh_cache():return
    SurveyDelivery.objects.filter(state='sending',attempted_at__lt=now-timedelta(minutes=2)).update(state='unknown',result='응답 확인 필요 · 자동 재발송하지 않습니다.')
    jobs=SurveyDelivery.objects.filter(state='pending').select_related('participation__survey','participation__employee__user__workhub_access')[:5]
    for job in jobs:
        if not SurveyDelivery.objects.filter(pk=job.pk,state='pending').update(state='sending',attempted_at=now):continue
        part=SurveyParticipation.objects.select_related('survey','employee__user__workhub_access').get(pk=job.participation_id)
        survey=part.survey;employee=part.employee;user=employee.user;access=getattr(user,'workhub_access',None) if user else None
        if part.submitted or survey.state!='open' or not survey.starts_at<=now<survey.closes_at or not(employee.is_active and user and access and access.verified_at and user.email.lower()==access.email.lower() and account_allowed(user)):
            job.state='skipped';job.result='제출 완료·마감 또는 현재 수신 자격 변경으로 제외'
        else:
            title=('설문 참여 안내' if job.stage=='initial' else '설문 응답 기한 안내')+' · '+survey.title
            url=settings.WORKHUB_BASE_URL.rstrip('/')+reverse('survey_detail',args=[survey.pk])
            try:
                if job.channel=='email':
                    html=render_to_string('tasks/email_survey.html',{'survey':survey,'title':title,'url':url})
                    deliver('[Workhub] '+title,f'{title}\n응답 마감: {timezone.localtime(survey.closes_at):%Y-%m-%d %H:%M}\n{url}',access.email,html_body=html)
                else:
                    link=HiworksLink.objects.filter(employee=employee,email__iexact=access.email).first()
                    if not link:raise ValidationError('하이웍스 직원 연결을 확인하세요.')
                    api('/office/v2/notify',settings.HIWORKS_NOTIFY_TOKEN,{'user_list':[link.user_id],'message':title,'link':url,'mlink':url,'solution_name':'GUNSYSTEMS Workhub'})
            except Exception:
                job.state='unknown';job.result='발송 결과 확인 필요 · 중복 방지를 위해 자동 재발송하지 않습니다.'
            else:job.state='sent';job.result='발송 서비스 접수 · 실제 수신과 구분됩니다.'
        job.save(update_fields=['state','result'])
