"""Scoped access, versioned actions and immutable evidence submissions."""
from datetime import datetime, time, timedelta
from pathlib import Path
from django import forms
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction, IntegrityError
from django.db.models import Q, Exists, OuterRef
from django.http import FileResponse, HttpResponse, Http404
from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse
from django.utils import timezone
from django.utils.html import format_html
from accounts.lifecycle import system_admin, account_allowed
from .models import Employee, OffboardingCase, OffboardingItem, OffboardingEvent, OffboardingEvidence, OffboardingNotice
from .drive_storage import configured, RepositoryError
from .documents import validate_file

DEFAULTS = [
    ('Workhub 계정 중지','계정 관리에서 사용 중지하고 기존 로그인 세션 종료 여부를 확인하세요.'),
    ('회사 메일 정리','메일·공용 주소·자동 전달과 연동을 확인하고 자료 인계 후 계정을 중지하세요. 보존 여부를 검토한 뒤 삭제하세요.'),
    ('Microsoft 365 계정 정리','OneDrive·공유 자료를 인계하고 로그인 차단·세션 종료·라이선스 회수 여부를 확인하세요.'),
    ('캡스 출입 권한 삭제','출입 사용자와 등록 수단을 삭제하고 실제 출입 단말기에 반영됐는지 확인하세요.'),
    ('업무·고객·문서 인계','진행 업무와 담당 고객, 문서 및 회사 계정의 인계 결과를 확인하세요.'),
    ('회사 자산 회수','PC·출입카드·열쇠 등 지급 자산의 반납 여부를 확인하세요.'),
]

def actor_name(user):
    e=getattr(user,'employee',None)
    return f'{e.department.name} · {e.name}' if e else user.get_username()

def admins():
    return [u for u in get_user_model().objects.filter(is_active=True,is_staff=True) if system_admin(u)]

def owners():
    ids=[u.pk for u in get_user_model().objects.filter(is_active=True).filter(Q(is_staff=True)|Q(employee__is_active=True)).distinct() if account_allowed(u)]
    return get_user_model().objects.filter(pk__in=ids).order_by('username')

class OwnerChoice(forms.ModelChoiceField):
    def label_from_instance(self,obj): return actor_name(obj)

class CaseForm(forms.ModelForm):
    class Meta:
        model=OffboardingCase
        fields=['employee','departure_date']
        labels={'employee':'퇴사 직원'}
        widgets={'departure_date':forms.DateInput(attrs={'type':'date'},format='%Y-%m-%d')}
    def __init__(self,*a,**kw):
        super().__init__(*a,**kw)
        self.fields['employee'].queryset=Employee.objects.exclude(user__is_superuser=True).select_related('department').order_by('name')

class ItemForm(forms.ModelForm):
    assignee=OwnerChoice(queryset=get_user_model().objects.none(),label='처리 담당자')
    class Meta:
        model=OffboardingItem
        fields=['title','instruction','assignee','due_at']
        widgets={'instruction':forms.Textarea(attrs={'rows':3}),'due_at':forms.DateTimeInput(attrs={'type':'datetime-local'},format='%Y-%m-%dT%H:%M')}
    def __init__(self,*a,case=None,**kw):
        super().__init__(*a,**kw)
        self.case=case
        self.fields['assignee'].queryset=owners().exclude(pk=case.employee.user_id) if case else owners()
    def clean_due_at(self):
        value=self.cleaned_data['due_at']
        if value < timezone.now(): raise ValidationError('새 배정 기한은 현재 이후로 지정하세요.')
        return value

class SubmitForm(forms.Form):
    processed_at=forms.DateTimeField(label='실제 처리 일시',widget=forms.DateTimeInput(attrs={'type':'datetime-local'},format='%Y-%m-%dT%H:%M'))
    body=forms.CharField(label='처리 내용',max_length=5000,widget=forms.Textarea(attrs={'rows':4}))
    evidence=forms.FileField(label='처리 증빙 · 이미지 또는 PDF, 최대 20MB',validators=[validate_file],widget=forms.ClearableFileInput(attrs={'accept':'.png,.jpg,.jpeg,.pdf','data-upload-limit':'20971520'}))
    def clean_processed_at(self):
        value=self.cleaned_data['processed_at']
        if value>timezone.now():raise ValidationError('미래 시각을 처리 일시로 기록할 수 없습니다.')
        return value
    def clean_evidence(self):
        f=self.cleaned_data['evidence']
        if Path(f.name).suffix.lower() not in ['.png','.jpg','.jpeg','.pdf']:raise ValidationError('PNG·JPG·PDF 파일을 첨부하세요.')
        if not configured():raise ValidationError('자료 저장소 연결을 확인한 뒤 제출하세요.')
        return f

def visible_items(user):
    qs=OffboardingItem.objects.select_related('case__employee__department','assignee')
    return qs if system_admin(user) else qs.filter(assignee=user)

def notice(item,users,key,text):
    for u in users:
        if u and u.pk!=item.case.employee.user_id and account_allowed(u):
            OffboardingNotice.objects.get_or_create(item=item,user=u,key=key,defaults={'message':text})

def record(case,user,action,body='',item=None,snapshot=None,processed_at=None):
    event=OffboardingEvent.objects.create(case=case,item=item,actor=user,actor_name=actor_name(user),action=action,body=body,snapshot=snapshot or {},processed_at=processed_at)
    from .audit import write
    write('update','OffboardingCase',case.pk,'퇴사 후속 조치',after={'처리':action,'항목':item.pk if item else None,'기록':event.pk},actor=user)
    return event

def check_version(obj,value):
    if str(obj.revision)!=str(value):raise ValidationError('다른 변경이 반영되었습니다. 화면을 새로고침한 뒤 다시 확인하세요.')

@transaction.atomic
def create_case(user,employee,departure_date):
    if not system_admin(user):raise PermissionDenied
    if employee.user_id and employee.user.is_superuser:raise PermissionDenied
    case=OffboardingCase.objects.create(employee=employee,departure_date=departure_date,creator=user)
    due=timezone.make_aware(datetime.combine(departure_date,time(17)),timezone.get_current_timezone())
    for title,instruction in DEFAULTS:
        item=OffboardingItem.objects.create(case=case,title=title,instruction=instruction,assignee=user,due_at=due)
        notice(item,[user],f'assigned:{item.revision}','퇴사 후속 조치가 배정되었습니다.')
    record(case,user,'퇴사 처리 등록')
    return case

@transaction.atomic
def change_item(pk,user,action,version,body='',data=None):
    # Lock the case before the item in every mutating path.
    seed=get_object_or_404(visible_items(user),pk=pk)
    case=OffboardingCase.objects.select_for_update().get(pk=seed.case_id)
    item=OffboardingItem.objects.select_for_update().get(pk=pk)
    if not system_admin(user) and item.assignee_id!=user.pk:raise PermissionDenied
    if action=='ack_cancel':
        if item.assignee_id!=user.pk or case.state!='cancelled':raise PermissionDenied
        if item.events.filter(actor=user,action='퇴사 취소 확인').exists():return item
        check_version(item,version)
        record(case,user,'퇴사 취소 확인',item=item)
        item.revision+=1;item.save(update_fields=['revision'])
        OffboardingNotice.objects.filter(item=item,user=user,read_at__isnull=True).update(read_at=timezone.now())
        return item
    check_version(item,version)
    if case.state!='active':raise ValidationError('종료되거나 취소된 퇴사 처리입니다.')
    reviewer=system_admin(user)
    submitter=reviewer or item.assignee_id==user.pk
    if action=='start' and submitter and item.state in ['pending','returned']:
        item.state='working';record(case,user,'처리 시작',item=item)
    elif action=='submit' and submitter and item.state in ['pending','working','returned']:
        event=record(case,user,'처리 결과 제출',body=data['body'],item=item,processed_at=data['processed_at'],snapshot={'담당자':item.assignee_id,'기한':item.due_at.isoformat(),'항목':item.title})
        f=data['evidence']
        OffboardingEvidence.objects.create(event=event,file=f,name=Path(f.name).name[:255])
        item.state='submitted';notice(item,admins(),f'review:{item.revision}','처리 증빙의 관리자 확인이 필요합니다.')
    elif action in ['approve','return'] and reviewer and item.state=='submitted':
        if action=='return' and not body.strip():raise ValidationError('보완이 필요한 내용을 입력하세요.')
        submission=item.events.filter(action='처리 결과 제출').first()
        item.state='approved' if action=='approve' else 'returned'
        record(case,user,'확인 완료' if action=='approve' else '보완 요청',body=body[:5000],item=item,snapshot={'제출 기록':submission.pk})
        notice(item,[item.assignee],f'decision:{item.revision}','관리자 확인이 완료되었습니다.' if action=='approve' else '처리 증빙에 보완 요청이 있습니다.')
    elif action=='waive' and reviewer and item.state in ['pending','working','returned']:
        if not body.strip():raise ValidationError('해당 없음으로 처리하는 사유를 입력하세요.')
        item.state='approved';record(case,user,'해당 없음 · 관리자 확인',body=body[:5000],item=item)
        notice(item,[item.assignee],f'decision:{item.revision}','관리자가 해당 없음으로 확인했습니다.')
    else:raise PermissionDenied
    item.revision+=1;item.save(update_fields=['state','revision'])
    case.revision+=1;case.save(update_fields=['revision'])
    return item

@login_required
def case_list(request):
    if not system_admin(request.user):raise PermissionDenied
    qs=OffboardingCase.objects.select_related('employee__department').prefetch_related('items')
    state=request.GET.get('state','active')
    if state in ['active','done','cancelled']:qs=qs.filter(state=state)
    rows=[{'case':c,'done':sum(i.state=='approved' for i in c.items.all()),'total':c.items.count(),'late':sum(i.overdue for i in c.items.all()),'review':sum(i.state=='submitted' for i in c.items.all())} for c in qs[:200]]
    return render(request,'tasks/offboarding_list.html',{'rows':rows,'state':state})

@login_required
def case_new(request):
    if not system_admin(request.user):raise PermissionDenied
    form=CaseForm(request.POST or None)
    if request.method=='POST' and form.is_valid():
        try:case=create_case(request.user,**form.cleaned_data)
        except IntegrityError:form.add_error(None,'이미 진행 중인 퇴사 처리가 있습니다.')
        else:return redirect('offboarding_case',pk=case.pk)
    return render(request,'tasks/offboarding_form.html',{'form':form,'title':'퇴사 처리 등록','back_url':reverse('offboarding_list'),'guide':'기본 후속 조치 6개가 관리자에게 배정됩니다. 등록 후 항목별 담당자와 기한을 조정하세요. 외부 계정은 자동 변경되지 않습니다.'})

@login_required
def case_detail(request,pk):
    if not system_admin(request.user):raise PermissionDenied
    case=get_object_or_404(OffboardingCase.objects.select_related('employee__department'),pk=pk)
    form=CaseForm(instance=case);form.fields['employee'].disabled=True
    if request.method=='POST':
        try:
            with transaction.atomic():
                case=OffboardingCase.objects.select_for_update().get(pk=pk);check_version(case,request.POST.get('revision'))
                action=request.POST.get('action');reason=request.POST.get('reason','').strip()
                if case.state!='active':raise ValidationError('이미 종료되거나 취소된 처리입니다.')
                if action=='finish':
                    if not case.items.exists() or case.items.exclude(state='approved').exists():raise ValidationError('모든 항목의 관리자 확인을 마쳐야 최종 완료할 수 있습니다.')
                    case.state='done';case.completed_at=timezone.now();record(case,request.user,'퇴사 처리 최종 완료')
                elif action=='cancel':
                    if not reason:raise ValidationError('취소 사유를 입력하세요.')
                    case.state='cancelled';record(case,request.user,'퇴사 처리 취소',reason[:5000])
                elif action=='date':
                    old=case.departure_date
                    form=CaseForm(request.POST,instance=case);form.fields['employee'].disabled=True
                    if not form.is_valid():raise ValidationError('퇴사일을 확인하세요.')
                    if not reason:raise ValidationError('퇴사일 변경 사유를 입력하세요.')
                    case.departure_date=form.cleaned_data['departure_date']
                    record(case,request.user,'퇴사일 변경',reason[:5000],snapshot={'이전':old.isoformat(),'변경':case.departure_date.isoformat()})
                else:raise PermissionDenied
                case.revision+=1;case.save()
            messages.success(request,'처리 내용을 저장했습니다.');return redirect('offboarding_case',pk=pk)
        except ValidationError as exc:messages.error(request,' '.join(exc.messages))
    case.refresh_from_db()
    return render(request,'tasks/offboarding_case.html',{'case':case,'date_form':form,'events':case.events.filter(item__isnull=True),'can_finish':case.items.exists() and not case.items.exclude(state='approved').exists()})

@login_required
def item_edit(request,case_pk,pk=None):
    if not system_admin(request.user):raise PermissionDenied
    case=get_object_or_404(OffboardingCase,pk=case_pk,state='active')
    item=get_object_or_404(OffboardingItem,pk=pk,case=case) if pk else None
    if item and item.state in ['submitted','approved']:raise PermissionDenied
    form=ItemForm(request.POST or None,instance=item,case=case)
    if request.method=='POST' and form.is_valid():
        try:
            with transaction.atomic():
                case=OffboardingCase.objects.select_for_update().get(pk=case_pk)
                if case.state!='active':raise ValidationError('종료된 처리입니다.')
                if item:
                    current=OffboardingItem.objects.select_for_update().get(pk=pk)
                    check_version(current,request.POST.get('revision'))
                    if current.state in ['submitted','approved']:raise ValidationError('확인 대기 또는 완료 항목은 변경할 수 없습니다.')
                elif case.items.count()>=30:raise ValidationError('처리 항목은 최대 30개입니다.')
                before={'담당자':current.assignee_id,'기한':current.due_at.isoformat(),'항목':current.title} if item else {}
                obj=form.save(commit=False);obj.case=case;obj.state='pending';obj.revision=(current.revision+1) if item else 0;obj.save()
                record(case,request.user,'항목 변경' if item else '항목 추가',item=obj,snapshot={'이전':before,'변경':{'담당자':obj.assignee_id,'기한':obj.due_at.isoformat(),'항목':obj.title}})
                notice(obj,[obj.assignee],f'assigned:{obj.revision}','퇴사 후속 조치가 배정·변경되었습니다.')
                case.revision+=1;case.save(update_fields=['revision'])
            return redirect('offboarding_case',pk=case_pk)
        except ValidationError as exc:form.add_error(None,exc)
    return render(request,'tasks/offboarding_form.html',{'form':form,'title':'후속 조치 담당자·기한 설정','revision':item.revision if item else 0,'back_url':reverse('offboarding_case',args=[case_pk])})

@login_required
def assignments(request):
    qs=visible_items(request.user).filter(assignee=request.user).annotate(cancel_ack=Exists(
        OffboardingEvent.objects.filter(item_id=OuterRef('pk'),actor=request.user,action='퇴사 취소 확인')))
    box=request.GET.get('box','active')
    if box not in ['active','cancelled','history']:box='active'
    active=Q(case__state='active') & ~Q(state='approved')
    cancelled=Q(case__state='cancelled',cancel_ack=False)
    if box=='active':items=qs.filter(active)
    elif box=='cancelled':items=qs.filter(cancelled)
    else:items=qs.filter(Q(case__state='done')|Q(state='approved',case__state='active')|Q(case__state='cancelled',cancel_ack=True))
    return render(request,'tasks/offboarding_assignments.html',{'items':items.order_by('due_at')[:200],
        'box':box,'active_count':qs.filter(active).count(),'cancelled_count':qs.filter(cancelled).count()})

@login_required
def item_detail(request,pk):
    item=get_object_or_404(visible_items(request.user),pk=pk)
    reviewer=system_admin(request.user)
    form=SubmitForm(initial={'processed_at':timezone.localtime().replace(second=0,microsecond=0)})
    if request.method=='POST':
        action=request.POST.get('action')
        if action=='submit':form=SubmitForm(request.POST,request.FILES)
        if action!='submit' or form.is_valid():
            try:
                change_item(pk,request.user,action,request.POST.get('revision'),request.POST.get('body',''),form.cleaned_data if action=='submit' else None)
            except (ValidationError,RepositoryError) as exc:
                messages.error(request,' '.join(exc.messages) if isinstance(exc,ValidationError) else str(exc))
            else:
                if action=='ack_cancel':return redirect('offboarding_assignments')
                return redirect('offboarding_item',pk=pk)
    item=get_object_or_404(visible_items(request.user),pk=pk)
    OffboardingNotice.objects.filter(item=item,user=request.user,read_at__isnull=True).update(read_at=timezone.now())
    return render(request,'tasks/offboarding_item.html',{'item':item,'cancel_event':item.case.events.filter(action='퇴사 처리 취소',item__isnull=True).first(),'cancel_ack':item.events.filter(actor=item.assignee,action='퇴사 취소 확인').first(),'can_ack_cancel':item.case.state=='cancelled' and item.assignee_id==request.user.pk,'form':form,'reviewer':reviewer,'events':item.events.prefetch_related('evidence'),'submission':item.events.filter(action='처리 결과 제출').prefetch_related('evidence').first(),'deliveries':OffboardingNotice.objects.filter(item=item).select_related('user')[:30] if reviewer else []})

@login_required
def evidence_file(request,pk,preview=False):
    evidence=get_object_or_404(OffboardingEvidence.objects.select_related('event'),pk=pk)
    get_object_or_404(visible_items(request.user),pk=evidence.event.item_id)
    try:
        if preview:
            import io,warnings
            from PIL import Image,ImageOps,UnidentifiedImageError
            try:
                with evidence.file.open('rb') as source,warnings.catch_warnings():
                    warnings.simplefilter('error',Image.DecompressionBombWarning)
                    with Image.open(source) as img:
                        if img.format not in ['PNG','JPEG'] or img.width*img.height>16000000:raise ValueError
                        img=ImageOps.exif_transpose(img);img.thumbnail((960,720));out=io.BytesIO();img.convert('RGB').save(out,format='JPEG',quality=85)
                response=HttpResponse(out.getvalue(),content_type='image/jpeg')
            except (UnidentifiedImageError,OSError,ValueError,Image.DecompressionBombWarning,Image.DecompressionBombError):
                return HttpResponse('미리보기를 지원하지 않습니다. 파일을 내려받아 확인하세요.',status=422,content_type='text/plain; charset=utf-8')
        else:response=FileResponse(evidence.file.open('rb'),as_attachment=True,filename=evidence.name,content_type='application/octet-stream')
    except (FileNotFoundError,RepositoryError):return HttpResponse('자료를 불러오지 못했습니다. 잠시 후 다시 시도하세요.',status=503)
    response['Cache-Control']='private, no-store';response['X-Content-Type-Options']='nosniff'
    return response

def queue_reminders(now=None):
    now=now or timezone.now()
    for item in OffboardingItem.objects.filter(case__state='active').exclude(state='approved').select_related('case__employee','assignee'):
        day=timezone.localdate(now).isoformat()
        if item.state=='submitted':
            notice(item,admins(),f'review-day:{day}','확인 대기 중인 퇴사 후속 조치 증빙이 있습니다.')
        elif item.due_at<=now:
            notice(item,[item.assignee]+admins(),f'overdue:{day}','처리 기한이 지난 퇴사 후속 조치가 있습니다.')
        elif item.due_at<=now+timedelta(days=1):
            notice(item,[item.assignee],f'due:{day}','퇴사 후속 조치의 처리 기한이 다가옵니다.')

def deliver_notices(now=None):
    from django.core.mail import send_mail
    now=now or timezone.now()
    # At-most-once dispatch claim. Unknown network outcomes are never blindly retried.
    for row in OffboardingNotice.objects.filter(mail_state='pending').select_related('user','item__case__employee')[:50]:
        if not OffboardingNotice.objects.filter(pk=row.pk,mail_state='pending').update(mail_state='sending',attempted_at=now):continue
        if row.item.case.state!='active' or not account_allowed(row.user) or not row.user.email or (not system_admin(row.user) and row.item.assignee_id!=row.user_id):
            OffboardingNotice.objects.filter(pk=row.pk).update(mail_state='skipped');continue
        url=settings.WORKHUB_BASE_URL.rstrip('/')+reverse('offboarding_item',args=[row.item_id])
        try:
            from django.template.loader import render_to_string
            html=render_to_string('tasks/email_offboarding.html',{'message':row.message,'url':url})
            sent=send_mail('GUNSYSTEMS Workhub · 후속 조치 알림',f'{row.message}\n{url}',settings.DEFAULT_FROM_EMAIL,[row.user.email],html_message=html)
            state='sent' if sent else 'failed'
        except Exception:state='failed'
        OffboardingNotice.objects.filter(pk=row.pk).update(mail_state=state)
        from .audit import write
        write('update','OffboardingNotice',row.pk,'후속 조치 알림',after={'메일 상태':state,'알림 번호':row.pk})
