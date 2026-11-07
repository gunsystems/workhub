"""Administrator-approved OTP recovery, bound to a password-authenticated session."""
import secrets
from datetime import timedelta

from django.contrib.auth.decorators import login_required
from django.contrib.auth.hashers import make_password, check_password
from django.db import transaction
from django.shortcuts import redirect
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.debug import sensitive_post_parameters

from .models import AccessProfile, OTPResetChallenge
from .services import allow, deliver, digest


def recovery_proven(request, access):
    proof = request.session.get('otp_restore_proof', {})
    return bool(access.otp_reset_key and proof.get('key') == access.otp_reset_key
                and 0 <= timezone.now().timestamp() - proof.get('at', 0) < 600)


@login_required
@never_cache
@sensitive_post_parameters('code')
def restore(request):
    from .views import CodeForm, page
    access = getattr(request.user, 'workhub_access', None)
    if not access or not access.otp_reset_key:
        return redirect('mfa_setup')
    if recovery_proven(request, access):
        return redirect('mfa_setup')
    if not request.session.get('otp_restore_nonce'):
        request.session['otp_restore_nonce'] = secrets.token_urlsafe(32)
    binding = digest(request.session['otp_restore_nonce'])
    form = CodeForm(request.POST or None)
    notice = '관리자가 OTP를 초기화했습니다. 회사 이메일로 인증한 후 새 휴대폰에 OTP를 등록하세요.'
    if request.method == 'POST':
        with transaction.atomic():
            access = AccessProfile.objects.select_for_update().get(pk=access.pk)
            challenge = OTPResetChallenge.objects.select_for_update().filter(user=request.user).first()
            if request.POST.get('action') == 'send':
                if challenge and timezone.now() - challenge.sent_at < timedelta(seconds=60):
                    form.add_error(None, '재발송은 60초 후 가능합니다.')
                elif not allow(f'restore:{request.user.pk}', 5, 3600):
                    form.add_error(None, '발송 요청이 많습니다. 1시간 후 다시 시도하세요.')
                else:
                    code = f'{secrets.randbelow(1000000):06d}'
                    challenge, _ = OTPResetChallenge.objects.update_or_create(user=request.user, defaults={
                        'reset_key':access.otp_reset_key, 'session_digest':binding,
                        'code_digest':make_password(code), 'expires_at':timezone.now()+timedelta(minutes=10),
                        'sent_at':timezone.now(), 'attempts':0})
                    try:
                        deliver('Workhub OTP 재등록 이메일 인증',
                                f'인증 코드: {code}\n10분 동안 유효합니다. 관리자에게 본인 확인 후 요청한 OTP 재등록에만 사용하세요.', access.email)
                    except Exception:
                        challenge.code_digest = ''
                        challenge.save(update_fields=['code_digest'])
                        form.add_error(None, '메일 발송에 실패했습니다. 관리자에게 문의하세요.')
                    else:
                        form = CodeForm()
                        notice = '회사 이메일로 인증 코드를 보냈습니다. 10분 안에 입력하세요.'
            elif form.is_valid():
                valid = bool(challenge and challenge.reset_key == access.otp_reset_key
                             and challenge.session_digest == binding and challenge.code_digest
                             and challenge.expires_at > timezone.now() and challenge.attempts < 5
                             and check_password(form.cleaned_data['code'], challenge.code_digest))
                if challenge:
                    challenge.attempts = min(challenge.attempts + 1, 5)
                    if valid:
                        challenge.code_digest = ''
                    challenge.save(update_fields=['attempts', 'code_digest'])
                if valid:
                    request.session['otp_restore_proof'] = {'key':access.otp_reset_key, 'at':timezone.now().timestamp()}
                    return redirect('mfa_setup')
                form.add_error('code', '코드가 올바르지 않거나 만료됐습니다. 최대 5회 입력할 수 있습니다.')
    return page(request, 'OTP 재등록 · 회사 이메일 인증', form, send_code=True, notice=notice)
