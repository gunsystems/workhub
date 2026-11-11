"""Short requests: explicit acknowledgement, result, then requester confirmation."""
from datetime import timedelta
from django import forms
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied,ValidationError
from django.db import transaction
from django.db.models import Q
from django.shortcuts import render,redirect,get_object_or_404
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from .models import QuickRequest,QuickEvent,QuickDelivery,Employee,HiworksLink
from .permissions import profile,can_view_company
from .documents import validate_file
from .drive_storage import configured,RepositoryError


def identity(user):
    employee=profile(user)
    return f'{employee.department} · {employee.name}' if employee else user.get_username()


def visible(user):
    rows=QuickRequest.objects.select_related('requester','assignee__department','assignee__user')
    if can_view_company(user):return rows
    employee=profile(user)
    scope=Q(requester=user)|Q(assignee__user=user)
    if employee and employee.department_id and (employee.role=='manager' or employee.position=='leader'):
        scope|=Q(assignee__department_id=employee.department_id)|Q(requester__employee__department_id=employee.department_id)
    return rows.filter(scope)


class QuickForm(forms.ModelForm):
    field_order=['title','body','assignee','attachment','link','send_email','send_hiworks']
    body=forms.CharField(label='요청 내용',max_length=10000,widget=forms.Textarea(attrs={'rows':5}))
    attachment=forms.FileField(label='참고 파일 · 최대 20MB',required=False,validators=[validate_file],widget=forms.ClearableFileInput(attrs={'data-upload-limit':'20971520','accept':'.pdf,.doc,.docx,.xls,.xlsx,.ppt,.pptx,.txt,.csv,.png,.jpg,.jpeg,.zip'}))
    link=forms.URLField(label='기사 · 웹페이지 링크',required=False,max_length=2000)
    class Meta:
        model=QuickRequest
        fields=['title','body','assignee','send_email','send_hiworks']
        labels={'assignee':'담당자'}
        widgets={'body':forms.Textarea(attrs={'rows':5})}
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        self.fields['assignee'].queryset=Employee.objects.filter(is_active=True,user__is_active=True,user__workhub_access__verified_at__isnull=False).select_related('department')
    def clean_assignee(self):
        employee=self.cleaned_data['assignee']
        from accounts.lifecycle import account_allowed
        access=employee.user.workhub_access
        if employee.user.email.lower()!=access.email.lower() or not account_allowed(employee.user):
            raise ValidationError('현재 이용 가능한 인증 직원으로 지정하세요.')
        return employee
    def clean_attachment(self):
        value=self.cleaned_data.get('attachment')
        if value and not configured():raise ValidationError('자료 저장소 연결 후 파일을 등록할 수 있습니다. 관리자에게 문의하세요.')
        return value
    def clean_link(self):
        value=self.cleaned_data.get('link','')
        if value and not value.lower().startswith(('https://','http://')):raise ValidationError('http 또는 https 링크를 입력하세요.')
        return value


def record(item,user,action,body=''):
    event=QuickEvent.objects.create(request=item,actor=user,actor_name=identity(user),action=action,body=body)
    targets={item.requester_id,item.assignee.user_id}-{None,user.pk}
    from django.contrib.auth import get_user_model
    for recipient in get_user_model().objects.filter(pk__in=targets,is_active=True):
        QuickDelivery.objects.get_or_create(event=event,user=recipient,channel='app',defaults={'state':'sent'})
        access=getattr(recipient,'workhub_access',None)
        if not access or not access.verified_at:continue
        for channel,enabled in [('email',item.send_email),('hiworks',item.send_hiworks)]:
            if enabled:QuickDelivery.objects.get_or_create(event=event,user=recipient,channel=channel,defaults={'email':access.email})
    from .audit import write
    write('quick_change','QuickRequest',item.pk,item.title,actor=user,after={'state':item.state,'action':action,'revision':item.revision})
    return event


@transaction.atomic
def create(user,data,files=None):
    from accounts.lifecycle import account_allowed
    if not account_allowed(user) or not (user.is_superuser or profile(user)):raise PermissionDenied
    form=QuickForm(data,files)
    if not form.is_valid():return form,None
    item=form.save(commit=False);item.requester=user;item.requester_name=identity(user);item.save()
    from .documents import add_document
    if form.cleaned_data.get('attachment'):add_document(user,'quick',item,{'file':form.cleaned_data['attachment'],'kind':'reference'})
    if form.cleaned_data.get('link'):add_document(user,'quick',item,{'link':form.cleaned_data['link'],'name':'참고 링크','kind':'reference'})
    record(item,user,'전달',item.body)
    return form,item


@transaction.atomic
def change(pk,user,action,body,revision):
    from accounts.lifecycle import account_allowed
    if not account_allowed(user):raise PermissionDenied
    item=QuickRequest.objects.select_for_update().select_related('assignee__user').get(pk=pk)
    if not visible(user).filter(pk=pk).exists():raise PermissionDenied
    if str(item.revision)!=str(revision):raise ValidationError('다른 처리로 내용이 변경되었습니다. 새로고침 후 확인하세요.')
    owner=item.requester_id==user.pk;worker=item.assignee.user_id==user.pk
    body=(body or '').strip()
    if len(body)>10000:raise ValidationError('내용은 10,000자 이내로 입력하세요.')
    if item.state in ['done','cancelled']:raise ValidationError('종료된 간단 업무입니다.')
    now=timezone.now()
    if action=='accept' and worker and item.state=='pending':
        item.state='working';item.accepted_at=now;label='요청 확인'
    elif action=='reply' and worker and item.state=='working':
        if not body:raise ValidationError('결과 내용을 입력하세요.')
        item.state='replied';item.replied_at=now;label='결과 회신'
    elif action=='confirm' and owner and item.state in ['working','replied']:
        if item.state=='working' and not body:body='전달된 의견·자료를 요청자가 확인하여 완료했습니다. 별도의 결과 회신 시각은 기록하지 않습니다.'
        item.state='done';item.completed_at=now;label='확인 완료'
    elif action=='return' and owner and item.state=='replied':
        if not body:raise ValidationError('추가 확인할 내용을 입력하세요.')
        item.state='working';label='추가 확인 요청'
    elif action=='cancel' and owner:
        if not body:raise ValidationError('취소 사유를 입력하세요.')
        item.state='cancelled';item.cancelled_at=now;label='취소'
    elif action=='comment' and (owner or worker):
        if not body:raise ValidationError('질문·의견을 입력하세요.')
        label='질문·의견'
    else:raise PermissionDenied
    item.revision+=1;item.save();record(item,user,label,body)
    return item


@login_required
def listing(request):
    rows=visible(request.user)
    box=request.GET.get('box','involved');state=request.GET.get('state','active');query=request.GET.get('q','').strip()
    if box=='sent':rows=rows.filter(requester=request.user)
    elif box=='received':rows=rows.filter(assignee__user=request.user)
    elif box!='all':rows=rows.filter(Q(requester=request.user)|Q(assignee__user=request.user))
    if state=='active':rows=rows.exclude(state__in=['done','cancelled'])
    elif state in dict(QuickRequest._meta.get_field('state').choices):rows=rows.filter(state=state)
    if query:rows=rows.filter(Q(title__icontains=query)|Q(body__icontains=query))
    return render(request,'tasks/quick_list.html',{'items':rows[:200],'box':box,'state':state,'query':query,'states':QuickRequest._meta.get_field('state').choices})


@login_required
def new(request):
    if request.method=='POST':
        try:form,item=create(request.user,request.POST,request.FILES)
        except RepositoryError as exc:
            form=QuickForm(request.POST,request.FILES);form.is_valid();form.add_error('attachment',str(exc));item=None
        if item:return redirect('quick_detail',pk=item.pk)
    else:form=QuickForm()
    return render(request,'tasks/quick_form.html',{'form':form})


@login_required
def detail(request,pk):
    item=get_object_or_404(visible(request.user),pk=pk)
    if request.method=='POST':
        try:change(pk,request.user,request.POST.get('action'),request.POST.get('body'),request.POST.get('revision'))
        except ValidationError as exc:messages.error(request,' '.join(exc.messages))
        return redirect('quick_detail',pk=pk)
    QuickDelivery.objects.filter(event__request=item,user=request.user,channel='app',read_at__isnull=True).update(read_at=timezone.now())
    from .documents import can_add
    step={'pending':2,'working':3,'replied':4,'done':4}.get(item.state,0)
    return render(request,'tasks/quick_detail.html',{'item':item,'step':step,'owner':item.requester_id==request.user.pk,'worker':item.assignee.user_id==request.user.pk,'active':item.state not in ['done','cancelled'],'events':item.events.all(),'documents':item.documents.filter(archived_at__isnull=True),'can_add_documents':can_add(request.user,'quick',item)})


def deliver_pending(now=None):
    from accounts.hiworks_access import fresh_cache
    from accounts.lifecycle import account_allowed
    from accounts.services import deliver
    from .hiworks import api
    now=now or timezone.now()
    if settings.WORKHUB_REQUIRE_HIWORKS and not fresh_cache():return
    QuickDelivery.objects.filter(state='sending',attempted_at__lt=now-timedelta(minutes=2)).update(state='unknown',result='발송 결과 확인 필요 · 자동 재발송하지 않습니다.')
    for job in QuickDelivery.objects.filter(state='pending').exclude(channel='app').select_related('event__request','user')[:10]:
        if not QuickDelivery.objects.filter(pk=job.pk,state='pending').update(state='sending',attempted_at=now):continue
        item=job.event.request;user=job.user;access=getattr(user,'workhub_access',None) if user else None
        entitled=user and user.pk in {item.requester_id,item.assignee.user_id}
        if not(entitled and access and access.verified_at and access.email.lower()==job.email.lower()==user.email.lower() and account_allowed(user)):
            job.state='skipped';job.result='현재 수신 자격 변경으로 발송하지 않았습니다.'
        elif item.state in ['done','cancelled'] and job.event.action not in ['확인 완료','취소']:
            job.state='skipped';job.result='종료된 요청의 이전 단계 알림입니다.'
        else:
            url=settings.WORKHUB_BASE_URL.rstrip('/')+reverse('quick_detail',args=[item.pk])
            try:
                if job.channel=='email':
                    title=f'간단 업무 · {job.event.action}'
                    html=render_to_string('tasks/email_quick.html',{'item':item,'event':job.event,'url':url,'title':title})
                    deliver('[Workhub] '+title,f'{title}\n{item.title}\n{url}',access.email,html_body=html)
                else:
                    employee=profile(user)
                    link=HiworksLink.objects.filter(employee=employee,email__iexact=access.email,notifications_enabled=True).first() if employee else None
                    if not link:
                        job.state='skipped';job.result='하이웍스 알림이 꺼져 있거나 직원 연결이 없습니다.';job.save(update_fields=['state','result']);continue
                    api('/office/v2/notify',settings.HIWORKS_NOTIFY_TOKEN,{'user_list':[link.user_id],'message':'Workhub: 확인할 간단 업무가 있습니다.','link':url,'mlink':url,'solution_name':'GUNSYSTEMS Workhub'})
            except Exception:job.state='unknown';job.result='발송 결과 확인 필요 · 중복 방지를 위해 자동 재발송하지 않습니다.'
            else:job.state='sent';job.result='발송 서비스가 접수했습니다.'
        job.save(update_fields=['state','result'])
