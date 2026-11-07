from django.conf import settings
from django.db import models
from django.utils import timezone


class AccessProfile(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='workhub_access')
    email = models.EmailField(unique=True)
    verified_at = models.DateTimeField(null=True)
    require_mfa = models.BooleanField(default=True)


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
