"""Independent opt-in Teams channel; existing mail/Hiworks records are untouched."""
from django.conf import settings
from django.db import models
from django.utils import timezone


class TeamsConnection(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    enabled = models.BooleanField(default=False)
    assignment = models.BooleanField(default=True)
    pending = models.BooleanField(default=True)
    result = models.BooleanField(default=True)
    tenant = models.CharField(max_length=36, blank=True)
    app = models.CharField(max_length=36, blank=True)
    subject = models.CharField(max_length=36, blank=True)
    conversation = models.CharField(max_length=500, blank=True)
    service_url = models.URLField(max_length=500, blank=True)
    email = models.EmailField(blank=True)
    generation = models.PositiveIntegerField(default=0)
    code_hash = models.CharField(max_length=64, blank=True, unique=True, null=True)
    code_expires_at = models.DateTimeField(null=True)
    connected_at = models.DateTimeField(null=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['tenant', 'app', 'subject'], condition=~models.Q(subject=''), name='teams_unique_person'),
            models.UniqueConstraint(fields=['tenant', 'app', 'conversation'], condition=~models.Q(conversation=''), name='teams_unique_chat'),
        ]


class TeamsDelivery(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    connection_generation = models.PositiveIntegerField()
    source = models.CharField(max_length=20)
    source_id = models.PositiveBigIntegerField()
    kind = models.CharField(max_length=12)
    dedupe_key = models.CharField(max_length=200, unique=True)
    snapshot = models.JSONField(default=dict)
    state = models.CharField(max_length=12, default='pending', db_index=True)
    attempts = models.PositiveSmallIntegerField(default=0)
    due_at = models.DateTimeField(default=timezone.now)
    claimed_until = models.DateTimeField(null=True)
    message_id = models.CharField(max_length=500, blank=True)
    outcome = models.CharField(max_length=40, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    sent_at = models.DateTimeField(null=True)

    class Meta:
        indexes = [models.Index(fields=['state', 'due_at'], name='teams_delivery_due')]
