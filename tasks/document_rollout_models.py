"""Execution journal, not an authorization source for employee file access."""
from django.conf import settings
from django.db import models
from django.utils import timezone


class DocumentRollout(models.Model):
    site_url = models.URLField(max_length=500, unique=True)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    scope_key = models.CharField(max_length=64)
    policy = models.CharField(max_length=40)
    state = models.CharField(max_length=20, default='prepared')
    data = models.JSONField(default=dict)
    cursor = models.PositiveIntegerField(default=0)
    revision = models.PositiveIntegerField(default=0)
    pending = models.CharField(max_length=200, blank=True)
    error = models.CharField(max_length=240, blank=True)
    lease_token = models.CharField(max_length=64, blank=True)
    lease_until = models.DateTimeField(default=timezone.now)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)
