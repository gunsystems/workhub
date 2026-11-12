"""Offboarding is an evidence/review workflow, never an external deletion engine."""
import secrets
from pathlib import Path
from django.conf import settings
from django.db import models
from django.utils import timezone


class OffboardingCase(models.Model):
    employee = models.ForeignKey('Employee', on_delete=models.PROTECT)
    departure_date = models.DateField('퇴사일')
    state = models.CharField(max_length=12, default='active', choices=[('active','진행 중'),('done','처리 완료'),('cancelled','취소')])
    creator = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    revision = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(default=timezone.now)
    completed_at = models.DateTimeField(null=True)
    class Meta:
        ordering = ['-created_at','-pk']
        constraints = [models.UniqueConstraint(fields=['employee'], condition=models.Q(state='active'), name='one_active_offboarding')]


class OffboardingItem(models.Model):
    case = models.ForeignKey(OffboardingCase, on_delete=models.PROTECT, related_name='items')
    title = models.CharField('처리 항목', max_length=160)
    instruction = models.TextField('처리 안내', blank=True)
    assignee = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='offboarding_assignments')
    due_at = models.DateTimeField('처리 기한')
    state = models.CharField(max_length=12, default='pending', choices=[('pending','처리 대기'),('working','처리 중'),('submitted','확인 대기'),('returned','보완 요청'),('approved','확인 완료')])
    revision = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(default=timezone.now)
    class Meta: ordering = ['pk']
    @property
    def overdue(self): return self.case.state == 'active' and self.state not in ['submitted','approved'] and self.due_at < timezone.now()


class OffboardingEvent(models.Model):
    case = models.ForeignKey(OffboardingCase, on_delete=models.PROTECT, related_name='events')
    item = models.ForeignKey(OffboardingItem, on_delete=models.PROTECT, null=True, related_name='events')
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    actor_name = models.CharField(max_length=180)
    action = models.CharField(max_length=60)
    body = models.TextField(blank=True)
    snapshot = models.JSONField(default=dict)
    processed_at = models.DateTimeField(null=True)
    created_at = models.DateTimeField(default=timezone.now)
    class Meta: ordering = ['-created_at','-pk']


def evidence_path(instance, filename):
    return f'offboarding/{instance.event.case_id}/{secrets.token_hex(24)}{Path(filename).suffix.lower()}'


class OffboardingEvidence(models.Model):
    event = models.ForeignKey(OffboardingEvent, on_delete=models.PROTECT, related_name='evidence')
    file = models.FileField(upload_to=evidence_path)
    name = models.CharField(max_length=255)
    class Meta: ordering = ['pk']
    @property
    def is_image(self): return Path(self.name).suffix.lower() in ['.png','.jpg','.jpeg']


class OffboardingNotice(models.Model):
    item = models.ForeignKey(OffboardingItem, on_delete=models.PROTECT)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    key = models.CharField(max_length=100)
    message = models.CharField(max_length=240)
    created_at = models.DateTimeField(default=timezone.now)
    read_at = models.DateTimeField(null=True)
    mail_state = models.CharField(max_length=12, default='pending')
    attempted_at = models.DateTimeField(null=True)
    class Meta:
        ordering = ['-created_at','-pk']
        constraints = [models.UniqueConstraint(fields=['item','user','key'],name='unique_offboarding_notice')]
