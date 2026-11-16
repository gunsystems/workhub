"""Private employee document requests; business approval roles grant no access."""
import secrets
from pathlib import Path
from django.conf import settings
from django.db import models
from django.utils import timezone
from django.core.files.storage import FileSystemStorage


class CertificateStorage(FileSystemStorage):
    def url(self, name):
        raise ValueError('Private certificate files have no public URL')


def certificate_storage():
    return CertificateStorage(location=Path(settings.MEDIA_ROOT) / 'employee-certificates')


def certificate_path(instance, filename):
    return f'{instance.pk}/{secrets.token_hex(24)}.pdf'


class CertificatePolicy(models.Model):
    assignee = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.PROTECT, related_name='+', verbose_name='기본 발급담당자')
    revision = models.PositiveIntegerField(default=0)


class CertificateRequest(models.Model):
    KINDS = [('employment', '재직증명서'), ('career', '경력증명서'),
             ('withholding', '원천징수영수증'), ('paystub', '급여명세서'), ('other', '기타 서류')]
    STATES = [('pending', '접수 대기'), ('processing', '발급 준비 중'), ('issued', '발급 완료'),
              ('rejected', '반려'), ('cancelled', '신청 취소'), ('revoked', '발급 취소')]
    employee = models.ForeignKey('Employee', on_delete=models.PROTECT, related_name='certificate_requests')
    submission_key = models.UUIDField(unique=True, editable=False)
    kind = models.CharField('서류 종류', max_length=16, choices=KINDS)
    document_name = models.CharField('필요한 서류명', max_length=120, blank=True)
    language = models.CharField('언어', max_length=4, choices=[('ko','국문'),('en','영문')], default='ko')
    purpose = models.CharField('발급 용도', max_length=200)
    destination = models.CharField('제출처', max_length=120, blank=True)
    needed_on = models.DateField('희망일', null=True, blank=True)
    period_start = models.DateField('대상 기간 시작', null=True, blank=True)
    period_end = models.DateField('대상 기간 종료', null=True, blank=True)
    notes = models.TextField('추가 요청', max_length=1000, blank=True)
    employee_snapshot = models.JSONField(default=dict)
    assignee = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.PROTECT, related_name='certificate_assignments')
    state = models.CharField(max_length=16, choices=STATES, default='pending')
    revision = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(default=timezone.now)
    issued_at = models.DateTimeField(null=True, blank=True)
    issued_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name='+')
    issued_file = models.FileField(storage=certificate_storage, upload_to=certificate_path, blank=True, max_length=255)
    issued_name = models.CharField(max_length=180, blank=True)
    class Meta:
        ordering = ['-created_at', '-pk']
    @property
    def title(self):
        return self.document_name if self.kind == 'other' else self.get_kind_display()
    @property
    def number(self):
        return f'WH-{self.created_at.year}-{self.pk:06d}'


class CertificateEvent(models.Model):
    request = models.ForeignKey(CertificateRequest, on_delete=models.PROTECT, related_name='events')
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    action = models.CharField(max_length=60)
    body = models.CharField(max_length=1000, blank=True)
    state = models.CharField(max_length=16)
    revision = models.PositiveIntegerField()
    created_at = models.DateTimeField(default=timezone.now)
    class Meta:
        ordering = ['-created_at', '-pk']


class CertificateNotice(models.Model):
    event = models.ForeignKey(CertificateEvent, on_delete=models.PROTECT)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    kind = models.CharField(max_length=12)
    email = models.EmailField(blank=True)
    message = models.CharField(max_length=180)
    read_at = models.DateTimeField(null=True)
    mail_state = models.CharField(max_length=12, default='pending')
    created_at = models.DateTimeField(default=timezone.now)
    class Meta:
        ordering = ['-created_at', '-pk']
        constraints = [models.UniqueConstraint(fields=['event','user','kind'], name='unique_certificate_notice')]
