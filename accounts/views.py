import base64
import io
import secrets
from datetime import timedelta

import qrcode
import django_otp
from django import forms
from django.contrib.auth import authenticate, get_user_model, login, update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import SetPasswordForm, PasswordResetForm
from django.contrib.auth.views import PasswordResetView, PasswordResetConfirmView
from django.contrib.auth.hashers import make_password, check_password
from django.core.exceptions import ValidationError
from django.db import transaction, IntegrityError
from django.http import HttpResponseBadRequest
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.debug import sensitive_post_parameters
from django_otp.plugins.otp_totp.models import TOTPDevice
from .models import Invitation, AccessProfile, RecoveryCode, OTPResetChallenge
from .services import digest, deliver, allow
from .lifecycle import account_allowed


def page(request, title, form=None, **context):
    return render(request, 'accounts/form.html', {'title': title, 'form': form, **context})


@never_cache
def csrf_failure(request, reason=''):
    # No provider details, tokens, submitted credentials or internal error text.
    response = page(request, '화면을 다시 열어 주세요',
                    notice='요청을 확인하지 못했습니다. 아래 버튼으로 화면을 다시 연 뒤 시도해 주세요. 입력한 비밀번호나 인증 코드는 처리되지 않았습니다.',
                    retry_url=request.path)
    response.status_code = 403
    response['Referrer-Policy'] = 'same-origin'
    return response


@login_required
@never_cache
def invitation_preview(request, employee_id):
    """Administrator-only design preview. Contains no usable activation token."""
    from django.core.exceptions import PermissionDenied
    from django.shortcuts import get_object_or_404
    from tasks.models import Employee
    if not request.user.is_superuser:
        raise PermissionDenied
    employee = get_object_or_404(Employee.objects.select_related('department'), pk=employee_id)
    invitation = getattr(employee, 'invitation', None)
    return render(request, 'accounts/email_invitation.html', {
        'employee_name': employee.name,
        'email': invitation.email if invitation else 'employee@gunsystems.co.kr',
        'department_name': employee.department.name, 'role_name': employee.get_role_display(),
        'activation_url': '#design-preview-only', 'expires_at': timezone.now()+timedelta(days=2),
    })


class LoginForm(forms.Form):
    username = forms.CharField(label='회사 이메일', max_length=254)
    password = forms.CharField(label='비밀번호', widget=forms.PasswordInput)


@login_required
@never_cache
def password_reset_preview(request):
    from django.core.exceptions import PermissionDenied
    if not request.user.is_superuser:
        raise PermissionDenied
    return render(request, 'accounts/email_password_reset.html', {
        'email':'employee@gunsystems.co.kr', 'reset_url':'#design-preview-only'})


class CodeForm(forms.Form):
    code = forms.CharField(label='인증 코드', max_length=32, widget=forms.TextInput(attrs={'autocomplete':'one-time-code'}))


class VerifiedPasswordResetForm(PasswordResetForm):
    def get_users(self, email):
        for user in super().get_users(email):
            access = getattr(user, 'workhub_access', None)
            if account_allowed(user) and access and access.verified_at and access.email == email.strip().lower():
                yield user

    def send_mail(self, subject_template_name, email_template_name, context, from_email, to_email, html_email_template_name=None):
        from django.template.loader import render_to_string
        from django.urls import reverse
        reset_path = reverse('password_reset_confirm', kwargs={'uidb64':context['uid'], 'token':context['token']})
        context = {**context, 'reset_url':f"{context['protocol']}://{context['domain']}{reset_path}"}
        deliver(''.join(render_to_string(subject_template_name, context).splitlines()),
                render_to_string(email_template_name, context), to_email,
                html_body=render_to_string(html_email_template_name, context) if html_email_template_name else None)


class ResetPassword(PasswordResetView):
    template_name = 'accounts/reset.html'
    email_template_name = 'accounts/reset_email.txt'
    html_email_template_name = 'accounts/email_password_reset.html'
    subject_template_name = 'accounts/reset_subject.txt'
    form_class = VerifiedPasswordResetForm
    success_url = '/accounts/password/reset/sent/'

    def form_valid(self, form):
        email = form.cleaned_data['email'].strip().lower()
        if not allow(f'reset-ip:{self.request.META.get("REMOTE_ADDR", "")}', 10, 3600) or not allow(f'reset-email:{email}', 3, 3600):
            return redirect(self.success_url)
        # Never derive password-reset links from an untrusted Host header.
        from urllib.parse import urlparse
        from django.conf import settings
        try:
            form.save(use_https=True, domain_override=urlparse(settings.WORKHUB_BASE_URL).netloc,
                      from_email=settings.DEFAULT_FROM_EMAIL, email_template_name=self.email_template_name,
                      subject_template_name=self.subject_template_name,
                      html_email_template_name=self.html_email_template_name)
        except Exception:
            # Same response for unknown addresses and temporary mail failures.
            pass
        return redirect(self.success_url)


class ConfirmResetPassword(PasswordResetConfirmView):
    template_name = 'accounts/reset.html'
    success_url = '/login/'

    def get_user(self, uidb64):
        user = super().get_user(uidb64)
        return user if account_allowed(user) else None


@never_cache
@sensitive_post_parameters('password')
def sign_in(request):
    if request.method == 'GET' and not request.user.is_authenticated and request.session.get('pending_mfa_user') and pending_user(request):
        return redirect('mfa_verify')
    form = LoginForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        identity = form.cleaned_data['username'].strip().lower()
        ip = request.META.get('REMOTE_ADDR', '')
        permitted = allow(f'login-ip:{ip}', 60, 900) and allow(f'login-id:{identity}', 10, 900)
        user = None
        if permitted:
            matches = get_user_model().objects.filter(email__iexact=identity) if '@' in identity else get_user_model().objects.filter(username=identity)
            candidate = matches.first() if matches.count() == 1 else None
            access = getattr(candidate, 'workhub_access', None) if candidate else None
            # New accounts must use their verified email. Preserve legacy credentials.
            if candidate and account_allowed(candidate) and (not access or (access.verified_at and identity == access.email)):
                user = authenticate(request, username=candidate.username, password=form.cleaned_data['password'])
            else:
                get_user_model()().set_password(form.cleaned_data['password'])
        if user:
            request.session.cycle_key()
            if django_otp.user_has_device(user):
                request.session['pending_mfa_user'] = user.pk
                request.session['pending_mfa_at'] = timezone.now().timestamp()
                return redirect('mfa_verify')
            login(request, user)
            access = getattr(user, 'workhub_access', None)
            return redirect('mfa_setup' if access and access.require_mfa else 'dashboard')
        form.add_error(None, '로그인할 수 없습니다. 입력 내용을 확인하거나 잠시 후 다시 시도하세요.')
    return page(request, 'Workhub 로그인', form)


@never_cache
@sensitive_post_parameters('code', 'new_password1', 'new_password2')
def activate(request, token):
    invitation = Invitation.objects.filter(token_digest=digest(token), used_at__isnull=True, expires_at__gt=timezone.now()).first()
    if not invitation or invitation.employee.user_id or not invitation.employee.is_active:
        return page(request, '사용할 수 없는 초대', notice='초대가 만료되었거나 이미 사용됐습니다. 관리자에게 재발송을 요청하세요.')
    if not request.session.get('activation_nonce'):
        request.session.cycle_key()
        request.session['activation_nonce'] = secrets.token_urlsafe(32)
    session_digest = digest(request.session['activation_nonce'])
    verified = bool(invitation.verified_session and invitation.verified_session == session_digest
                    and invitation.code_expires_at and invitation.code_expires_at > timezone.now())
    form = SetPasswordForm(get_user_model()(email=invitation.email, username=invitation.email), request.POST or None) if verified else CodeForm(request.POST or None)
    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'send' and not verified:
            ip = request.META.get('REMOTE_ADDR', '')
            if not allow(f'code-ip:{ip}', 30, 3600):
                form.add_error(None, '발송 요청이 많습니다. 잠시 후 다시 시도하세요.')
            else:
                with transaction.atomic():
                    invitation = Invitation.objects.select_for_update().get(pk=invitation.pk)
                    if invitation.token_digest != digest(token) or invitation.used_at or invitation.expires_at <= timezone.now():
                        return HttpResponseBadRequest('사용할 수 없는 초대입니다.')
                    if invitation.code_sent_at and timezone.now()-invitation.code_sent_at < timedelta(seconds=60):
                        form.add_error(None, '재발송은 60초 후 가능합니다.')
                    elif not allow(f'code:{invitation.pk}', 5, 3600):
                        form.add_error(None, '발송 한도를 초과했습니다. 1시간 후 다시 시도하세요.')
                    else:
                        code = f'{secrets.randbelow(1000000):06d}'
                        invitation.code_digest = make_password(code)
                        invitation.code_expires_at = timezone.now()+timedelta(minutes=10)
                        invitation.code_sent_at = timezone.now()
                        invitation.code_attempts = 0
                        invitation.verified_session = ''
                        invitation.save()
                        try:
                            deliver('Workhub 이메일 인증 코드', f'인증 코드: {code}\n10분 동안 유효합니다. 다른 사람에게 알려주지 마세요.', invitation.email)
                        except Exception:
                            invitation.code_digest = ''
                            invitation.save(update_fields=['code_digest'])
                            form.add_error(None, '메일 발송에 실패했습니다. 관리자에게 문의하세요.')
                        else:
                            return page(request, '회사 이메일 인증', CodeForm(), notice='회사 이메일로 인증 코드를 보냈습니다.', send_code=True)
        elif not verified and form.is_valid():
            with transaction.atomic():
                invitation = Invitation.objects.select_for_update().get(pk=invitation.pk)
                if invitation.token_digest != digest(token) or invitation.used_at or invitation.expires_at <= timezone.now():
                    return HttpResponseBadRequest('사용할 수 없는 초대입니다.')
                valid = (invitation.code_digest and invitation.code_expires_at and invitation.code_expires_at > timezone.now()
                         and invitation.code_attempts < 5 and check_password(form.cleaned_data['code'], invitation.code_digest))
                invitation.code_attempts = min(invitation.code_attempts + 1, 5)
                if valid:
                    invitation.verified_session = session_digest
                    invitation.code_digest = ''
                invitation.save()
            if valid:
                return redirect('activate', token=token)
            form.add_error('code', '코드가 올바르지 않거나 만료됐습니다. 최대 5회 입력할 수 있습니다.')
        elif verified and form.is_valid():
            try:
                with transaction.atomic():
                    invitation = Invitation.objects.select_for_update().get(pk=invitation.pk)
                    from tasks.models import Employee
                    employee = Employee.objects.select_for_update().get(pk=invitation.employee_id)
                    if (invitation.token_digest != digest(token) or invitation.used_at or invitation.expires_at <= timezone.now()
                            or employee.user_id or not employee.is_active or invitation.verified_session != session_digest
                            or not invitation.code_expires_at or invitation.code_expires_at <= timezone.now()):
                        return HttpResponseBadRequest('이미 사용되었거나 만료된 초대입니다.')
                    if get_user_model().objects.filter(email__iexact=invitation.email).exists() or get_user_model().objects.filter(username__iexact=invitation.email).exists():
                        raise IntegrityError('Email already registered')
                    user = get_user_model().objects.create_user(username=invitation.email, email=invitation.email, password=form.cleaned_data['new_password1'])
                    AccessProfile.objects.create(user=user, email=invitation.email, verified_at=timezone.now())
                    employee.user = user
                    employee.save(update_fields=['user'])
                    invitation.used_at = timezone.now()
                    invitation.verified_session = ''
                    invitation.save(update_fields=['used_at','verified_session'])
            except IntegrityError:
                form.add_error(None, '이미 등록된 이메일입니다. 관리자에게 문의하세요.')
            else:
                request.session.pop('activation_nonce', None)
                login(request, user, backend='django.contrib.auth.backends.ModelBackend')
                return redirect('mfa_setup')
    return page(request, '비밀번호 설정' if verified else '회사 이메일 인증', form, send_code=not verified)


def pending_user(request):
    if request.user.is_authenticated:
        return request.user if account_allowed(request.user) else None
    stamp = request.session.get('pending_mfa_at', 0)
    if timezone.now().timestamp()-stamp > 300:
        request.session.pop('pending_mfa_user', None)
        return None
    user = get_user_model().objects.filter(pk=request.session.get('pending_mfa_user'), is_active=True).first()
    return user if account_allowed(user) else None


def verify_factor(user, code):
    access = getattr(user, 'workhub_access', None)
    if access and access.otp_reset_key:
        return None
    for device in TOTPDevice.objects.filter(user=user, confirmed=True):
        verified = django_otp.verify_token(user, device.persistent_id, code)
        if verified:
            return verified
    if not user.is_superuser:
        return None
    with transaction.atomic():
        for recovery in RecoveryCode.objects.select_for_update().filter(user=user):
            if check_password(code, recovery.digest):
                recovery.delete()
                return TOTPDevice.objects.filter(user=user, confirmed=True).first()
    return None


@never_cache
@sensitive_post_parameters('code')
def mfa_verify(request):
    user = pending_user(request)
    if not user:
        return redirect('login')
    if not django_otp.user_has_device(user):
        return redirect('mfa_setup' if request.user.is_authenticated else 'login')
    form = CodeForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        permitted = allow(f'otp:{user.pk}', 10, 300)
        device = verify_factor(user, form.cleaned_data['code'].strip()) if permitted else None
        if device:
            login(request, user, backend='django.contrib.auth.backends.ModelBackend')
            django_otp.login(request, device)
            request.session.pop('pending_mfa_user', None)
            request.session.pop('pending_mfa_at', None)
            return redirect('dashboard')
        form.add_error('code', 'OTP가 올바르지 않습니다. 잠시 후 다시 시도하세요.')
    return page(request, '2차 인증', form, notice='인증 앱의 6자리 OTP를 입력하세요. 휴대폰을 분실했거나 OTP를 사용할 수 없다면 회사 관리자에게 OTP 초기화를 요청하세요.')


@login_required
@never_cache
@sensitive_post_parameters('code')
def mfa_setup(request):
    if django_otp.user_has_device(request.user):
        return redirect('mfa_recovery' if request.user.is_verified() else 'mfa_verify')
    device = TOTPDevice.objects.filter(user=request.user, confirmed=False).first()
    if not device:
        device = TOTPDevice.objects.create(user=request.user, name='Workhub', confirmed=False)
    form = CodeForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        with transaction.atomic():
            access = AccessProfile.objects.select_for_update().filter(user=request.user).first()
            if access and access.otp_reset_key:
                from .reset_views import recovery_proven
                if not recovery_proven(request, access):
                    return redirect('mfa_restore')
            verified = django_otp.verify_token(request.user, device.persistent_id, form.cleaned_data['code']) if allow(f'otp-setup:{request.user.pk}', 10, 300) else None
            if verified:
                device.refresh_from_db()
                device.confirmed = True
                device.save(update_fields=['confirmed'])
                django_otp.login(request, device)
                RecoveryCode.objects.filter(user=request.user).delete()
                if access and access.otp_reset_key:
                    access.otp_reset_key = ''
                    access.save(update_fields=['otp_reset_key'])
                    OTPResetChallenge.objects.filter(user=request.user).delete()
                    # All sessions from before or during recovery lose authentication.
                    from .lifecycle import revoke_sessions
                    revoke_sessions(request.user.pk)
                    request.session.cycle_key()
                request.session.pop('otp_restore_proof', None)
                request.session.pop('otp_restore_nonce', None)
                return page(request, 'OTP 등록 완료', enrollment_complete=True, notice='등록이 완료되었습니다. 앞으로 회사 이메일·비밀번호와 인증 앱의 OTP로 로그인하세요. 휴대폰을 분실하면 회사 관리자에게 OTP 초기화를 요청하세요.')
        form.add_error('code', 'OTP가 올바르지 않거나 시도 한도를 초과했습니다.')
    buffer = io.BytesIO()
    qrcode.make(device.config_url).save(buffer, format='PNG')
    return page(request, 'OTP 등록', form, qr=base64.b64encode(buffer.getvalue()).decode(),
                notice='Google Authenticator 등 인증 앱으로 QR코드를 스캔하고 6자리 코드를 입력하세요.')


@login_required
@never_cache
@sensitive_post_parameters('password', 'code')
def recovery(request):
    if not request.user.is_verified():
        return redirect('mfa_verify')
    class SecurityForm(CodeForm):
        password = forms.CharField(label='현재 비밀번호', widget=forms.PasswordInput)
        action = forms.ChoiceField(label='처리', choices=([('regenerate','관리자 비상 복구 코드 발급')] if request.user.is_superuser else []) + [('replace','휴대폰 변경 · OTP 다시 등록')])
    form = SecurityForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        permitted = allow(f'security:{request.user.pk}', 5, 900)
        if not permitted or not request.user.check_password(form.cleaned_data['password']) or not verify_factor(request.user, form.cleaned_data['code'].strip()):
            form.add_error(None, '비밀번호와 OTP를 확인하거나 잠시 후 다시 시도하세요.')
        elif form.cleaned_data['action'] == 'replace':
            access = getattr(request.user, 'workhub_access', None)
            if not access or not access.verified_at:
                form.add_error(None, '기존 관리자 계정의 OTP 교체는 관리자 지원 절차를 이용하세요.')
            else:
                with transaction.atomic():
                    TOTPDevice.objects.filter(user=request.user).delete()
                    RecoveryCode.objects.filter(user=request.user).delete()
                    access.require_mfa = True
                    access.save(update_fields=['require_mfa'])
                    # Rehashing invalidates every other session, including old verified sessions.
                    request.user.set_password(form.cleaned_data['password'])
                    request.user.save(update_fields=['password'])
                update_session_auth_hash(request, request.user)
                request.session.pop(django_otp.DEVICE_ID_SESSION_KEY, None)
                return redirect('mfa_setup')
        else:
            codes = [secrets.token_hex(8) for _ in range(8)]
            with transaction.atomic():
                RecoveryCode.objects.filter(user=request.user).delete()
                RecoveryCode.objects.bulk_create([RecoveryCode(user=request.user, digest=make_password(code)) for code in codes])
            return page(request, '새 복구 코드', codes=codes, notice='이전 복구 코드는 모두 무효화되었습니다. 새 코드는 지금 한 번만 표시됩니다.')
    return page(request, '계정 보안', form, notice='OTP가 등록되어 있습니다. 현재 인증 앱을 사용할 수 있으면 아래에서 휴대폰을 변경할 수 있습니다. OTP를 사용할 수 없다면 회사 관리자에게 본인 확인 후 초기화를 요청하세요.')
