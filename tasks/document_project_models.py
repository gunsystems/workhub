"""Only projects explicitly created through the standard document workflow."""
from django.conf import settings
from django.db import models
from django.utils import timezone


class DocumentProject(models.Model):
    project = models.OneToOneField('Project', on_delete=models.PROTECT, related_name='document_folder')
    site_url = models.URLField(max_length=500)
    name = models.CharField(max_length=40)
    normalized_name = models.CharField(max_length=40)
    creator = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+')
    administrator = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+')
    scope_key = models.CharField(max_length=64)
    metadata = models.JSONField(default=dict)
    participants = models.ManyToManyField('Employee', related_name='+')
    data = models.JSONField(default=dict)
    state = models.CharField(max_length=20, default='prepared')
    phase = models.CharField(max_length=12, default='create')
    cursor = models.PositiveIntegerField(default=0)
    revision = models.PositiveIntegerField(default=0)
    pending = models.CharField(max_length=120, blank=True)
    error = models.CharField(max_length=240, blank=True)
    lease_token = models.CharField(max_length=64, blank=True)
    lease_until = models.DateTimeField(default=timezone.now)
    close_requested_at = models.DateTimeField(null=True)
    close_requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.PROTECT, related_name='+')
    created_at = models.DateTimeField(default=timezone.now)
    closed_at = models.DateTimeField(null=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['site_url','normalized_name'], name='document_project_site_name')]
        ordering = ['-created_at','-pk']
