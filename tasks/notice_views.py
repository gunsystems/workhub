from django import forms
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied,ValidationError
from django.core.paginator import Paginator
from django.db import transaction,OperationalError
from django.shortcuts import get_object_or_404,redirect,render
from django.utils import timezone
from .models import Notice,NoticeRecipient,Employee,Department
from .permissions import is_company_admin
from .notices import targets,queue,cancel


class NoticeForm(forms.ModelForm):
    scheduled_for=forms.DateTimeField(label='예약 날짜·시간 · 한국 시간',required=False,
        widget=forms.DateTimeInput(format='%Y-%m-%dT%H:%M',attrs={'type':'datetime-local'}))
    body=forms.CharField(label='공지 내용',max_length=10000,widget=forms.Textarea(attrs={'rows':8}))
    class Meta:
        model=Notice
        fields=['title','body','audience','departments','employees','send_email','send_messenger','timing','scheduled_for']
        labels={'timing':'발송 시점','departments':'전달할 부서','employees':'전달할 직원'}
        widgets={'departments':forms.CheckboxSelectMultiple,'employees':forms.CheckboxSelectMultiple}

    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        self.fields['departments'].queryset=Department.objects.all().order_by('name')
        self.fields['employees'].queryset=Employee.objects.filter(is_active=True,user__is_active=True,
            user__workhub_access__verified_at__isnull=False).select_related('department').order_by('department__name','name')
        self.fields['employees'].help_text='초대·이메일 인증을 마친 직원에게 전달합니다.'
        self.fields['send_email'].help_text='회사 양식 이메일로 공지 내용과 확인 버튼을 보냅니다.'
        self.fields['send_messenger'].help_text='하이웍스 메신저로 공지 제목과 확인 링크를 보냅니다.'
        self.fields['scheduled_for'].initial=timezone.localtime().replace(second=0,microsecond=0)

    def clean(self):
        data=super().clean()
        if data.get('audience')=='team' and not data.get('departments'):self.add_error('departments','전달할 부서를 선택하세요.')
        if data.get('audience')=='person' and not data.get('employees'):self.add_error('employees','전달할 직원을 선택하세요.')
        if not data.get('send_email') and not data.get('send_messenger'):self.add_error(None,'이메일 또는 하이웍스 메신저를 하나 이상 선택하세요.')
        if data.get('timing')=='scheduled':
            if not data.get('scheduled_for'):self.add_error('scheduled_for','예약 날짜·시간을 입력하세요.')
            elif data['scheduled_for']<=timezone.now():self.add_error('scheduled_for','예약 시각은 현재 이후로 지정하세요.')
        else:data['scheduled_for']=timezone.now()
        if data.get('audience')!='team':data['departments']=Department.objects.none()
        if data.get('audience')!='person':data['employees']=Employee.objects.none()
        return data


@login_required
def notice_list(request):
    admin=is_company_admin(request.user)
    notices=Notice.objects.all() if admin else Notice.objects.filter(state='published',recipients__user=request.user,
        recipients__employee__user=request.user,published_at__lte=timezone.now()).distinct()
    return render(request,'tasks/notice_list.html',{'notices':Paginator(notices,25).get_page(request.GET.get('page')),'notice_admin':admin})


@login_required
def notice_edit(request,pk=None):
    if not is_company_admin(request.user):raise PermissionDenied
    notice=get_object_or_404(Notice,pk=pk) if pk else None
    if notice and notice.state!='draft':raise PermissionDenied('발송을 확정한 공지는 수정할 수 없습니다. 예약을 취소하고 다시 작성하세요.')
    form=NoticeForm(request.POST if request.method=='POST' else None,instance=notice)
    if request.method=='POST' and form.is_valid():
        try:
            with transaction.atomic():
                if notice and not Notice.objects.filter(pk=notice.pk,state='draft').exists():
                    raise ValidationError('다른 화면에서 발송 상태가 변경됐습니다. 목록에서 다시 확인하세요.')
                obj=form.save(commit=False)
                if not obj.pk:obj.creator=request.user;obj.creator_name=getattr(getattr(request.user,'employee',None),'name',None) or request.user.get_username()
                obj.save();form.save_m2m()
        except (ValidationError,OperationalError):form.add_error(None,'상태가 변경됐거나 저장이 지연됐습니다. 내용을 확인하고 다시 저장하세요.')
        else:return redirect('notice_detail',pk=obj.pk)
    return render(request,'tasks/notice_form.html',{'form':form,'notice':notice})


@login_required
def notice_detail(request,pk):
    admin=is_company_admin(request.user)
    notices=Notice.objects.all() if admin else Notice.objects.filter(state='published',published_at__lte=timezone.now(),
        recipients__user=request.user,recipients__employee__user=request.user)
    notice=get_object_or_404(notices.distinct(),pk=pk)
    error=''
    if request.method=='POST':
        if not admin:raise PermissionDenied
        try:
            if request.POST.get('action')=='send':queue(notice.pk,request.user)
            elif request.POST.get('action')=='cancel':cancel(notice.pk,request.user)
            else:raise ValidationError('처리 항목을 확인하세요.')
        except ValidationError as exc:error=exc.messages[0]
        except OperationalError:error='다른 처리가 진행 중입니다. 잠시 후 상태를 다시 확인하세요.'
        else:return redirect('notice_detail',pk=notice.pk)
        notice.refresh_from_db()
    if not admin:
        NoticeRecipient.objects.filter(notice=notice,user=request.user,read_at__isnull=True).update(read_at=timezone.now())
    recipients=list(notice.recipients.select_related('employee__department').prefetch_related('deliveries')) if admin else []
    preview=list(targets(notice)) if admin and notice.state=='draft' else []
    return render(request,'tasks/notice_detail.html',{'notice':notice,'notice_admin':admin,'recipients':recipients,
        'preview':preview,'recipient_count':len(preview) if notice.state=='draft' else len(recipients),'error':error})
