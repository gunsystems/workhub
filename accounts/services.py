import hashlib
import secrets
from datetime import timedelta
from urllib.parse import urlparse

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import make_password, check_password
from django.core.exceptions import ValidationError
from django.core.mail import send_mail
from django.db import transaction
from django.utils import timezone
from django.template.loader import render_to_string
from .models import Invitation, RateBucket, AccessProfile


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def company_email(value):
    email = value.strip().lower()
    if email.count('@') != 1 or email.split('@')[1] != settings.WORKHUB_EMAIL_DOMAIN:
        raise ValidationError('회사 이메일(@gunsystems.co.kr)을 입력하세요.')
    return email


@transaction.atomic
def allow(key, limit, seconds):
    now = timezone.now()
    bucket, _ = RateBucket.objects.select_for_update().get_or_create(
        key=digest(key), defaults={'expires_at': now + timedelta(seconds=seconds)})
    if bucket.expires_at <= now:
        bucket.count = 0
        bucket.expires_at = now + timedelta(seconds=seconds)
    bucket.count += 1
    bucket.save()
    return bucket.count <= limit


def deliver(subject, body, recipient, html_body=None):
    # Never print invitation links or verification codes to production logs.
    if settings.EMAIL_BACKEND.endswith(('console.EmailBackend', 'filebased.EmailBackend')):
        raise ValidationError('실제 메일 발송 연결이 필요합니다. 로그 출력 방식으로 인증 메일을 보내지 않습니다.')
    if settings.EMAIL_BACKEND == 'django.core.mail.backends.smtp.EmailBackend' and not (
            settings.EMAIL_HOST and settings.EMAIL_HOST_USER and settings.EMAIL_HOST_PASSWORD):
        raise ValidationError('SMTP 발신 계정과 인증정보를 서버 비밀 설정에 등록하세요.')
    if send_mail(subject, body, settings.DEFAULT_FROM_EMAIL, [recipient], fail_silently=False, html_message=html_body) != 1:
        raise ValidationError('메일 발송 서비스가 요청을 접수하지 못했습니다.')


def invite(employee, email):
    email = company_email(email)
    if employee.user_id:
        raise ValidationError('이미 계정이 연결된 직원입니다. 기존 계정은 덮어쓰지 않습니다.')
    if AccessProfile.objects.filter(email__iexact=email).exists() or get_user_model().objects.filter(email__iexact=email).exists():
        raise ValidationError('이미 계정에 사용 중인 이메일입니다.')
    base = settings.WORKHUB_BASE_URL.rstrip('/')
    parsed = urlparse(base)
    if not parsed.netloc or parsed.scheme != 'https':
        raise ValidationError('HTTPS 시스템 주소(WORKHUB_BASE_URL)를 설정하세요.')
    if not allow(f'invite:{employee.pk}', 5, 3600):
        raise ValidationError('초대 발송 한도를 초과했습니다. 1시간 후 다시 시도하세요.')
    token = secrets.token_urlsafe(32)
    with transaction.atomic():
        invitation, _ = Invitation.objects.update_or_create(employee=employee, defaults={
            'email': email, 'token_digest': digest(token), 'expires_at': timezone.now()+timedelta(days=2),
            'used_at': None, 'code_digest': '', 'code_expires_at': None, 'code_attempts': 0,
            'code_sent_at': None, 'verified_session': '', 'delivery': 'pending', 'sent_at': None})
    try:
        activation_url = f'{base}/accounts/activate/{token}/'
        html_body = render_to_string('accounts/email_invitation.html', {
            'employee_name': employee.name, 'email': email,
            'department_name': employee.department.name, 'role_name': employee.get_role_display(),
            'activation_url': activation_url, 'expires_at': invitation.expires_at,
        })
        deliver('[GUNSYSTEMS] Workhub 계정 활성화 안내',
                f'{employee.name}님, 회사 업무관리 시스템에 초대되었습니다.\n'
                f'소속: {employee.department.name} / 계정: {email}\n'
                '메일의 계정 활성화하기 버튼에서 이메일 인증, 비밀번호 설정 및 OTP 등록을 완료하세요.\n'
                '초대는 48시간 동안 유효합니다. 버튼이 보이지 않으면 HTML 메일 보기로 전환해 주세요.\n'
                '요청하지 않은 초대라면 회사 관리자에게 문의하세요.', email, html_body=html_body)
    except Exception:
        Invitation.objects.filter(pk=invitation.pk).update(delivery='failed')
        raise ValidationError('초대 메일 발송에 실패했습니다. 메일 설정 확인 후 재발송하세요.')
    Invitation.objects.filter(pk=invitation.pk).update(delivery='accepted', sent_at=timezone.now())
