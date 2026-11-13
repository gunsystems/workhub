import uuid
from django.conf import settings
from django.db import models
from django.utils import timezone


class MicrosoftConnection(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    subject = models.CharField(max_length=150)
    email = models.EmailField()
    encrypted_refresh_token = models.TextField()
    connected_at = models.DateTimeField(default=timezone.now)


class Meeting(models.Model):
    task = models.OneToOneField('Task', on_delete=models.CASCADE, related_name='meeting')
    organizer = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    mode = models.CharField(max_length=12, choices=[('office','사내'),('outside','외부'),('online','온라인'),('hybrid','온·오프라인 병행')])
    provider = models.CharField(max_length=12, default='link', choices=[('link','링크 직접 입력'),('teams','Teams 자동 생성')])
    join_url = models.URLField(max_length=2000, blank=True)
    guests = models.JSONField(default=list)
    result = models.TextField(blank=True)
    event_id = models.CharField(max_length=1000, blank=True)
    transaction_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    sync_state = models.CharField(max_length=16, default='local', choices=[('local','Workhub 등록'),('pending','Teams 연결 대기'),('sending','Teams 처리 중'),('synced','Teams 연결 완료'),('error','Teams 연결 확인 필요'),('unknown','Teams 처리 결과 확인 필요')])
    synced_revision = models.IntegerField(default=-1)
    sync_error = models.CharField(max_length=240, blank=True)
    claimed_at = models.DateTimeField(null=True)
    revision = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(default=timezone.now)


class MeetingAttendee(models.Model):
    meeting = models.ForeignKey(Meeting, on_delete=models.CASCADE, related_name='attendees')
    employee = models.ForeignKey('Employee', on_delete=models.PROTECT)
    response = models.CharField(max_length=12, default='pending', choices=[('pending','응답 대기'),('yes','참석'),('maybe','미정'),('no','불참')])
    responded_at = models.DateTimeField(null=True)
    active = models.BooleanField(default=True)
    reminded_revision = models.IntegerField(default=-1)
    class Meta:
        constraints = [models.UniqueConstraint(fields=['meeting','employee'], name='unique_meeting_attendee')]


class MeetingMail(models.Model):
    meeting = models.ForeignKey(Meeting, on_delete=models.CASCADE, related_name='mails')
    revision = models.PositiveIntegerField()
    email = models.EmailField()
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True)
    removed = models.BooleanField(default=False)
    state = models.CharField(max_length=12, default='pending')
    created_at = models.DateTimeField(default=timezone.now)
    class Meta:
        constraints = [models.UniqueConstraint(fields=['meeting','revision','email'], name='unique_meeting_mail')]
