from django.conf import settings
from django.db import models
from django.utils import timezone


class WorkDocument(models.Model):
    task = models.ForeignKey('Task', null=True, blank=True, on_delete=models.PROTECT, related_name='documents')
    work_request = models.ForeignKey('WorkRequest', null=True, blank=True, on_delete=models.PROTECT, related_name='documents')
    project = models.ForeignKey('Project', null=True, blank=True, on_delete=models.PROTECT, related_name='documents')
    quick_request = models.ForeignKey('QuickRequest', null=True, blank=True, on_delete=models.PROTECT, related_name='documents')
    file = models.FileField(upload_to='documents', blank=True)
    link = models.URLField(max_length=2000, blank=True)
    name = models.CharField(max_length=200)
    original_name = models.CharField(max_length=255,blank=True)
    kind = models.CharField(max_length=20, choices=[('reference','참고 자료'),('working','진행 자료'),('result','결과물'),('inspection','점검확인서'),('other','기타')], default='reference')
    size = models.PositiveIntegerField(default=0)
    uploader = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    uploader_name = models.CharField(max_length=150)
    created_at = models.DateTimeField(default=timezone.now)
    archived_at = models.DateTimeField(null=True)
    archive_reason = models.CharField(max_length=500, blank=True)

    @property
    def preview_candidate(self):
        from pathlib import Path
        suffix=Path(self.original_name or self.name).suffix.lower()
        return bool(self.file) and (not suffix or suffix in ['.png','.jpg','.jpeg','.gif','.webp'])

    class Meta:
        ordering = ['-created_at','-pk']
        constraints = [models.CheckConstraint(condition=(
            models.Q(task__isnull=False, work_request__isnull=True, project__isnull=True,quick_request__isnull=True) |
            models.Q(task__isnull=True, work_request__isnull=False, project__isnull=True,quick_request__isnull=True) |
            models.Q(task__isnull=True, work_request__isnull=True, project__isnull=False,quick_request__isnull=True) |
            models.Q(task__isnull=True, work_request__isnull=True, project__isnull=True,quick_request__isnull=False)), name='document_one_parent')]
