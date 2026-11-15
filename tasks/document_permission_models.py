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
