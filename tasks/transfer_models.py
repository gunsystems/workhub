from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils import timezone


class TaskTransfer(models.Model):
    STATES = [('pending', '이관 승인 대기'), ('acceptance', '인수 확인 대기'),
              ('done', '인수 완료'), ('rejected', '반려'), ('withdrawn', '요청 취소')]
    task = models.ForeignKey('Task', on_delete=models.PROTECT, related_name='transfers')
    source = models.ForeignKey('Employee', on_delete=models.PROTECT, related_name='+')
    target = models.ForeignKey('Employee', on_delete=models.PROTECT, related_name='+')
    requester = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+')
    reason = models.TextField(max_length=2000)
    notes = models.TextField(max_length=10000, blank=True)
    primary = models.BooleanField(default=False)
    source_assignment = models.JSONField(default=dict)
    state = models.CharField(max_length=16, choices=STATES, default='pending')
    revision = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(default=timezone.now)
    approved_at = models.DateTimeField(null=True)
    accepted_at = models.DateTimeField(null=True)
    closed_at = models.DateTimeField(null=True)

    class Meta:
        ordering = ['-created_at', '-pk']
        constraints = [models.UniqueConstraint(fields=['task', 'source'],
            condition=Q(state__in=['pending', 'acceptance']), name='one_open_task_transfer_per_source')]


class TransferStep(models.Model):
    transfer = models.ForeignKey(TaskTransfer, on_delete=models.CASCADE, related_name='steps')
    approver = models.ForeignKey('Employee', on_delete=models.PROTECT, related_name='+')
    position = models.PositiveSmallIntegerField()
    label = models.CharField(max_length=80)
    decision = models.CharField(max_length=16, default='pending')
    note = models.CharField(max_length=2000, blank=True)
    decided_at = models.DateTimeField(null=True)

    class Meta:
        ordering = ['position']
        constraints = [models.UniqueConstraint(fields=['transfer', 'position'], name='unique_transfer_step_position')]
