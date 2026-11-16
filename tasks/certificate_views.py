import uuid
from django import forms
from django.core import signing
from django.core.exceptions import PermissionDenied, ValidationError
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.http import FileResponse, HttpResponse
from django.shortcuts import get_object_or_404, render, redirect
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods, require_GET
from accounts.lifecycle import system_admin
from .models import CertificateRequest, CertificatePolicy, CertificateNotice
from .permissions import profile
from .leave_views import employee_access
from . import certificates


class RequestForm(forms.ModelForm):
    submission = forms.CharField(widget=forms.HiddenInput)
    class Meta:
        model = CertificateRequest
        fields = ['kind','document_name','language','purpose','destination','needed_on','period_start','period_end','notes']
        widgets = {name: forms.DateInput(format='%Y-%m-%d', attrs={'type':'date'}) for name in ['needed_on','period_start','period_end']}
        widgets['notes'] = forms.Textarea(attrs={'rows':3})
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['purpose'].help_text = '예: 금융기관 제출, 자격 심사, 개인 보관'
        self.fields['document_name'].help_text = '기타 서류를 선택했을 때만 입력하세요.'
        self.fields['destination'].help_text = '제출할 기관이 있으면 적어 주세요.'
        self.fields['period_start'].help_text = '경력·급여·세무 서류의 필요한 기간이 있으면 선택하세요.'
        self.fields['notes'].help_text = '포함할 경력이나 필요한 기재사항을 알려 주세요. 주민등록번호 등 민감한 정보는 적지 마세요.'
    def clean(self):
        data = super().clean()
        if data.get('kind') == 'other' and not data.get('document_name'):
            self.add_error('document_name', '필요한 서류명을 입력하세요.')
        if data.get('kind') != 'other':
            data['document_name'] = ''
        start, end = data.get('period_start'), data.get('period_end')
        if bool(start) != bool(end):
            self.add_error('period_end', '대상 기간은 시작일과 종료일을 함께 선택하세요.')
        if start and end and end < start:
            self.add_error('period_end', '종료일은 시작일 이후로 선택하세요.')
        if data.get('needed_on') and data['needed_on'] < timezone.localdate():
            self.add_error('needed_on', '희망일은 오늘 이후로 선택하세요.')
        return data


class HandlerChoice(forms.ModelChoiceField):
    def label_from_instance(self, obj):
        person = profile(obj)
        return f'{person} · {obj.email}' if person else f'{obj.get_username()} · 관리자'


class HandlerForm(forms.Form):
    revision = forms.IntegerField(min_value=0, widget=forms.HiddenInput)
    assignee = HandlerChoice(label='발급담당자', queryset=None, required=False, empty_label='지정하지 않음 · 관리자에게 접수')
    transfer_open = forms.BooleanField(label='진행 중인 신청도 함께 재배정', required=False)
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['assignee'].queryset = certificates.eligible_users()


class ActionForm(forms.Form):
    revision = forms.IntegerField(min_value=0, widget=forms.HiddenInput)
    action = forms.ChoiceField(choices=[(a,a) for a in ['start','issue','reject','reassign','revoke','cancel']], widget=forms.HiddenInput)
    body = forms.CharField(label='처리 의견', max_length=1000, required=False, widget=forms.Textarea(attrs={'rows':2}))
    file = forms.FileField(label='발급할 PDF', required=False, widget=forms.ClearableFileInput(attrs={'accept':'.pdf','data-upload-limit':'20971520'}))
    assignee = HandlerChoice(label='새 담당자', queryset=None, required=False, empty_label='관리자에게 접수')
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['assignee'].queryset = certificates.eligible_users()
    def clean(self):
        data = super().clean()
        if data.get('action') == 'issue':
            try:
                certificates.validate_pdf(data.get('file'))
            except ValidationError as exc:
                self.add_error('file', exc)
        if data.get('action') in ['reject','revoke'] and not data.get('body','').strip():
            self.add_error('body', '사유를 입력하세요.')
        return data


@login_required
@never_cache
@employee_access
@require_http_methods(['GET','POST'])
def new(request):
    person = profile(request.user)
    if not person or request.user.is_superuser:
        raise PermissionDenied
    token = signing.dumps({'user':request.user.pk,'key':str(uuid.uuid4())}, salt='certificate-submit')
    form = RequestForm(request.POST or None, initial={'submission':token})
    if request.method == 'POST' and form.is_valid():
        try:
            payload = signing.loads(form.cleaned_data['submission'], salt='certificate-submit', max_age=86400)
            if payload.get('user') != request.user.pk:
                raise signing.BadSignature
            key = uuid.UUID(payload['key'])
            fields = {name:form.cleaned_data[name] for name in RequestForm.Meta.fields}
            item = certificates.submit(request.user, key, fields)
        except (signing.BadSignature, ValueError, KeyError, TypeError):
            form.add_error(None, '신청 화면이 만료되었습니다. 새로 열어 다시 신청하세요.')
        except ValidationError as exc:
            form.add_error(None, exc)
        else:
            messages.success(request, '서류 신청을 접수했습니다. 발급되면 알림으로 알려드립니다.')
            return redirect('certificate_detail', pk=item.pk)
    policy = CertificatePolicy.objects.select_related('assignee').filter(pk=1).first()
    handler = policy.assignee if policy and policy.assignee_id and certificates.handler_allowed(policy.assignee) else None
    return render(request, 'tasks/certificate_form.html', {'form':form,'person':person,
        'identity':certificates.snapshot(person),'handler':handler})


def portal_context(user, year):
    scope = certificates.visible(user)
    person = profile(user)
    own = scope.filter(employee=person) if person else scope.none()
    managed = scope.exclude(employee=person) if person else scope
    pending = certificates.pending_for(user)
    return {'certificate_own':own.filter(created_at__year=year), 'certificate_managed':managed.filter(created_at__year=year),
        'certificate_pending':pending, 'certificate_count':pending.count(),
        'certificate_can_apply':bool(person and not user.is_superuser),
        'certificate_admin':system_admin(user),
        'certificate_handler':bool(system_admin(user) or scope.filter(assignee=user).exists()),
        'certificate_history':scope.filter(created_at__year=year).exclude(state__in=certificates.OPEN)}


@login_required
@never_cache
@employee_access
@require_http_methods(['GET','POST'])
def detail(request, pk):
    item = get_object_or_404(certificates.visible(request.user), pk=pk)
    form = ActionForm(initial={'revision':item.revision})
    error = ''
    if request.method == 'POST':
        form = ActionForm(request.POST, request.FILES)
        if form.is_valid():
            try:
                certificates.act(pk, request.user, form.cleaned_data['action'], form.cleaned_data['revision'],
                    form.cleaned_data['body'], form.cleaned_data['file'], form.cleaned_data['assignee'])
            except ValidationError as exc:
                error = ' / '.join(exc.messages)
            except OSError:
                error = '발급 파일을 저장하지 못했습니다. 다시 시도해 주세요.'
            else:
                return redirect('certificate_detail', pk=pk)
            item.refresh_from_db()
    CertificateNotice.objects.filter(event__request=item, user=request.user, read_at__isnull=True).update(read_at=timezone.now())
    return render(request, 'tasks/certificate_detail.html', {'item':item,'form':form,'error':error,
        'owner':item.employee.user_id == request.user.pk,'handler':certificates.can_handle(request.user,item),
        'admin':system_admin(request.user),'open':item.state in certificates.OPEN,
        'events':item.events.select_related('actor')})


@login_required
@never_cache
@employee_access
@require_http_methods(['GET','POST'])
def settings_view(request):
    if not system_admin(request.user):
        raise PermissionDenied
    policy = CertificatePolicy.objects.filter(pk=1).first() or CertificatePolicy(pk=1)
    form = HandlerForm(request.POST or None, initial={'revision':policy.revision,'assignee':policy.assignee_id})
    if request.method == 'POST' and form.is_valid():
        try:
            count = certificates.set_policy(request.user, form.cleaned_data['assignee'],
                form.cleaned_data['revision'], form.cleaned_data['transfer_open'])
        except ValidationError as exc:
            form.add_error(None, exc)
        else:
            messages.success(request, f'담당자 설정을 저장했습니다. 진행 중 신청 {count}건을 재배정했습니다.')
            return redirect('certificate_settings')
    return render(request, 'tasks/certificate_settings.html', {'form':form})


@login_required
@never_cache
@employee_access
@require_GET
def download(request, pk):
    item = get_object_or_404(certificates.visible(request.user), pk=pk, state='issued')
    if not item.issued_file:
        return HttpResponse('발급 파일을 확인하지 못했습니다. 담당자에게 문의하세요.', status=404)
    try:
        response = FileResponse(item.issued_file.open('rb'), as_attachment=True,
            filename=item.issued_name, content_type='application/pdf')
    except OSError:
        return HttpResponse('발급 파일을 불러오지 못했습니다. 담당자에게 문의하세요.', status=503)
    response['Cache-Control'] = 'private, no-store'
    response['X-Content-Type-Options'] = 'nosniff'
    response['Content-Security-Policy'] = "default-src 'none'; sandbox"
    response['Referrer-Policy'] = 'no-referrer'
    from .audit import write
    write('download', 'CertificateRequest', item.pk, '발급 서류 다운로드', actor=request.user)
    return response
