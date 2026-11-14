from django.conf import settings
from django.db import models
from django.utils import timezone


class DocumentConnection(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    tenant = models.UUIDField()
    client = models.UUIDField()
    subject = models.UUIDField()
    email = models.EmailField()
    encrypted_refresh_token = models.TextField()
    connected_at = models.DateTimeField(default=timezone.now)


class SharedDocument(models.Model):
    # Identity survives renaming; filenames/URLs are never used as identifiers.
    site_url = models.URLField(max_length=500)
    drive_id = models.CharField(max_length=200)
    item_id = models.CharField(max_length=200)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['site_url', 'drive_id', 'item_id'], name='shared_document_identity')]


class SharedDocumentLink(models.Model):
    document = models.ForeignKey(SharedDocument, on_delete=models.PROTECT, related_name='work_links')
    task = models.ForeignKey('Task', null=True, blank=True, on_delete=models.PROTECT)
    project = models.ForeignKey('Project', null=True, blank=True, on_delete=models.PROTECT)
    creator = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        constraints = [
            models.CheckConstraint(condition=(models.Q(task__isnull=False, project__isnull=True) | models.Q(task__isnull=True, project__isnull=False)), name='shared_document_one_parent'),
            models.UniqueConstraint(fields=['document', 'task'], name='shared_document_task'),
            models.UniqueConstraint(fields=['document', 'project'], name='shared_document_project'),
        ]
