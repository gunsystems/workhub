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


class DocumentVendor(models.Model):
    site_url = models.URLField(max_length=500)
    name = models.CharField(max_length=80)
    normalized_name = models.CharField(max_length=80)
    is_active = models.BooleanField(default=True)
    ready = models.BooleanField(default=False)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['site_url','normalized_name'],name='document_vendor_site_name')]
        ordering = ['name','pk']

    def save(self,*args,**kwargs):
        from .document_structure import validate_name
        validate_name(self.name,limit=80)
        self.normalized_name=self.name.casefold()
        if kwargs.get('update_fields') is not None:
            kwargs['update_fields']=set(kwargs['update_fields'])|{'normalized_name'}
        return super().save(*args,**kwargs)
