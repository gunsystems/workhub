from django.conf import settings
from django.db import models
from django.utils import timezone


class Notice(models.Model):
    title=models.CharField('공지 제목',max_length=160)
    body=models.TextField('공지 내용')
    creator=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.SET_NULL,null=True)
    creator_name=models.CharField(max_length=150)
    audience=models.CharField('전달 대상',max_length=12,choices=[('all','전사'),('team','부서 선택'),('person','직원 선택')])
    departments=models.ManyToManyField('tasks.Department',blank=True)
    employees=models.ManyToManyField('tasks.Employee',blank=True)
    send_email=models.BooleanField('이메일',default=True)
    send_messenger=models.BooleanField('하이웍스 메신저',default=False)
    timing=models.CharField(max_length=12,default='now',choices=[('now','즉시 발송'),('scheduled','예약 발송')])
    scheduled_for=models.DateTimeField('발송 날짜·시간',default=timezone.now)
    created_at=models.DateTimeField(default=timezone.now)
    published_at=models.DateTimeField(null=True)
    state=models.CharField(max_length=16,default='draft',choices=[('draft','내용 확인 중'),('scheduled','발송 대기'),('published','공지 게시'),('cancelled','발송 취소')])
    class Meta:
        ordering=['-created_at','-pk']


class NoticeRecipient(models.Model):
    notice=models.ForeignKey(Notice,on_delete=models.PROTECT,related_name='recipients')
    employee=models.ForeignKey('tasks.Employee',on_delete=models.SET_NULL,null=True)
    user=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.SET_NULL,null=True)
    employee_name=models.CharField(max_length=80)
    email=models.EmailField()
    read_at=models.DateTimeField(null=True)
    class Meta:
        constraints=[models.UniqueConstraint(fields=['notice','user'],name='unique_notice_recipient')]


class NoticeDelivery(models.Model):
    recipient=models.ForeignKey(NoticeRecipient,on_delete=models.PROTECT,related_name='deliveries')
    channel=models.CharField(max_length=12,choices=[('email','이메일'),('messenger','하이웍스 메신저')])
    state=models.CharField(max_length=16,default='pending',choices=[('pending','발송 대기'),('sending','처리 중'),('sent','발송 접수'),('unknown','결과 확인 필요'),('skipped','발송 제외'),('cancelled','발송 취소')])
    claimed_until=models.DateTimeField(null=True)
    attempted_at=models.DateTimeField(null=True)
    result=models.CharField(max_length=160,blank=True)
    class Meta:
        constraints=[models.UniqueConstraint(fields=['recipient','channel'],name='unique_notice_delivery_channel')]
