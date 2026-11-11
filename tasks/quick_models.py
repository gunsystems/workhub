"""Date-free requests are deliberately separate from calendar/deadline tasks."""
from django.conf import settings
from django.db import models
from django.utils import timezone


class QuickRequest(models.Model):
    title=models.CharField('업무명',max_length=160)
    body=models.TextField('요청 내용')
    requester=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.SET_NULL,null=True,related_name='quick_requests_sent')
    requester_name=models.CharField(max_length=180)
    assignee=models.ForeignKey('tasks.Employee',on_delete=models.PROTECT,related_name='quick_requests')
    state=models.CharField(max_length=12,default='pending',choices=[('pending','확인 대기'),('working','처리 중'),('replied','결과 회신'),('done','확인 완료'),('cancelled','취소')])
    send_email=models.BooleanField('이메일로 배정 알림',default=True)
    send_hiworks=models.BooleanField('하이웍스로 배정 알림',default=True)
    revision=models.PositiveIntegerField(default=0)
    created_at=models.DateTimeField(default=timezone.now)
    accepted_at=models.DateTimeField(null=True)
    replied_at=models.DateTimeField(null=True)
    completed_at=models.DateTimeField(null=True)
    cancelled_at=models.DateTimeField(null=True)
    def __str__(self):return self.title
    class Meta: ordering=['-created_at','-pk']


class QuickEvent(models.Model):
    request=models.ForeignKey(QuickRequest,on_delete=models.PROTECT,related_name='events')
    actor=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.SET_NULL,null=True)
    actor_name=models.CharField(max_length=180)
    action=models.CharField(max_length=30)
    body=models.TextField(blank=True)
    created_at=models.DateTimeField(default=timezone.now)
    class Meta: ordering=['-created_at','-pk']


class QuickDelivery(models.Model):
    event=models.ForeignKey(QuickEvent,on_delete=models.CASCADE,related_name='deliveries')
    user=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.SET_NULL,null=True)
    email=models.EmailField(blank=True)
    channel=models.CharField(max_length=12)
    state=models.CharField(max_length=12,default='pending')
    read_at=models.DateTimeField(null=True)
    attempted_at=models.DateTimeField(null=True)
    result=models.CharField(max_length=180,blank=True)
    class Meta:
        constraints=[models.UniqueConstraint(fields=['event','user','channel'],name='unique_quick_event_delivery')]
