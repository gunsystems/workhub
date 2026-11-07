from django.conf import settings
from django.db import models
from django.utils import timezone
from django.contrib.auth.models import User


class AdministratorAccount(User):
    class Meta:
        proxy = True
        verbose_name = '관리자 계정'
        verbose_name_plural = '관리자 계정 관리'


class AccessProfile(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='workhub_access')
    email = models.EmailField(unique=True)
    verified_at = models.DateTimeField(null=True)
    require_mfa = models.BooleanField(default=True)
    otp_reset_key = models.CharField(max_length=64, blank=True)


class OTPResetChallenge(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    reset_key = models.CharField(max_length=64)
    session_digest = models.CharField(max_length=64)
    code_digest = models.CharField(max_length=128)
    expires_at = models.DateTimeField()
    sent_at = models.DateTimeField()
    attempts = models.PositiveSmallIntegerField(default=0)


class Invitation(models.Model):
    employee = models.OneToOneField('tasks.Employee', on_delete=models.CASCADE, related_name='invitation')
    email = models.EmailField(unique=True)
    token_digest = models.CharField(max_length=64)
    expires_at = models.DateTimeField()
    used_at = models.DateTimeField(null=True)
    code_digest = models.CharField(max_length=128, blank=True)
    code_expires_at = models.DateTimeField(null=True)
    code_attempts = models.PositiveSmallIntegerField(default=0)
    code_sent_at = models.DateTimeField(null=True)
    verified_session = models.CharField(max_length=64, blank=True)
    sent_at = models.DateTimeField(null=True)
    delivery = models.CharField(max_length=16, default='pending')

    @property
    def state(self):
        if self.used_at:
            return '사용 중'
        if self.delivery == 'failed':
            return '발송 실패'
        if self.expires_at <= timezone.now():
            return '초대 만료'
        return '인증 대기' if self.sent_at else '초대 대기'


class RateBucket(models.Model):
    key = models.CharField(max_length=64, unique=True)
    count = models.PositiveIntegerField(default=0)
    expires_at = models.DateTimeField()


class RecoveryCode(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    digest = models.CharField(max_length=128)


class AccountEvent(models.Model):
    ACTION_CHOICES = [('reset_mfa','본인 확인 완료 · OTP 초기화'), ('suspend','사용 중지'), ('reactivate','재활성화'),
                      ('delete_access','로그인 계정 삭제 · 업무 이력 보존'),
                      ('delete_employee','직원 완전 삭제 · 이력 없는 경우만'),
                      ('grant_admin','서브 관리자 지정'), ('revoke_admin','서브 관리자 해제')]
    employee = models.ForeignKey('tasks.Employee', null=True, on_delete=models.SET_NULL)
    employee_reference = models.PositiveBigIntegerField()
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    action = models.CharField(max_length=24, choices=ACTION_CHOICES)
    reason = models.CharField(max_length=1000)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ['-created_at', '-pk']
