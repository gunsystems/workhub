from django.conf import settings
from django.db import models
from django.utils import timezone


class DocumentPermissionConnection(models.Model):
    """Separate, delegated administrator connection; never used for employee reads."""
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    tenant = models.UUIDField()
    client = models.UUIDField()
    subject = models.UUIDField()
    email = models.EmailField()
    site_url = models.URLField(max_length=500)
    encrypted_refresh_token = models.TextField()
    connected_at = models.DateTimeField(default=timezone.now)


class DocumentPermissionLease(models.Model):
    target = models.CharField(max_length=64, unique=True)
    expires_at = models.DateTimeField()


class DocumentAccessGroup(models.Model):
    """References to groups created here, not a separate authorization database."""
    site_url = models.URLField(max_length=500)
    principal_id = models.PositiveIntegerField()
    name = models.CharField(max_length=100)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['site_url', 'principal_id'], name='document_group_site_id')]


class PermissionReportSnapshot(models.Model):
    """Administrator display only. NEVER consulted by document authorization."""
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    scope_key = models.CharField(max_length=64)
    site_url = models.URLField(max_length=500)
    payload = models.JSONField(default=dict)
    completed_at = models.DateTimeField(null=True)
    next_at = models.DateTimeField(default=timezone.now)
    attempted_at = models.DateTimeField(null=True)
    error = models.CharField(max_length=240, blank=True)
    progress = models.PositiveIntegerField(default=0)
    revision = models.PositiveIntegerField(default=0)
    lease_token = models.CharField(max_length=64, blank=True)
    lease_until = models.DateTimeField(default=timezone.now)


class PermissionReportName(models.Model):
    """Explicit display-name link, not identity authentication or an access grant."""
    site_url = models.URLField(max_length=500)
    principal_id = models.PositiveIntegerField()
    identity = models.CharField(max_length=500)
    directory_email = models.EmailField()

    class Meta:
        constraints = [models.UniqueConstraint(fields=['site_url', 'principal_id'], name='permission_report_name_site_id')]


class DocumentStaffGroup(models.Model):
    """Explicitly approved automation for one ordinary selected-site group."""
    group = models.OneToOneField(DocumentAccessGroup, on_delete=models.PROTECT)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    connection_key = models.CharField(max_length=64)
    enabled = models.BooleanField(default=False)
    bindings = models.JSONField(default=dict)
    revision = models.PositiveIntegerField(default=0)
    next_at = models.DateTimeField(default=timezone.now)
    last_checked_at = models.DateTimeField(null=True)
    error = models.CharField(max_length=240, blank=True)
    pending = models.PositiveIntegerField(default=0)
    lease_token = models.CharField(max_length=64, blank=True)
    lease_until = models.DateTimeField(default=timezone.now)
