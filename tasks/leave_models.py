"""Leave records are independent of task completion and attendance scoring."""
from django.conf import settings
from django.db import models
from django.utils import timezone

class StaffEmployment(models.Model):
    employee=models.OneToOneField('tasks.Employee',on_delete=models.PROTECT,related_name='employment')
    joined_on=models.DateField('입사일')
    ended_on=models.DateField('퇴사일',null=True,blank=True)
    staff_number=models.CharField('사번',max_length=40,blank=True)
    updated_at=models.DateTimeField(auto_now=True)

class LeaveYear(models.Model):
    year=models.PositiveSmallIntegerField('관리 연도',unique=True)
    calendar_confirmed=models.BooleanField('근무일·휴무일 확인 완료',default=False)
    note=models.CharField('운영 메모',max_length=500,blank=True)

class LeaveHoliday(models.Model):
    date=models.DateField('휴무일',unique=True)
    name=models.CharField('휴무일 이름',max_length=80)

class PublicHoliday(models.Model):
    date=models.DateField('공휴일',unique=True)
    name=models.CharField('공휴일 이름',max_length=200)
    active=models.BooleanField(default=True)
    updated_at=models.DateTimeField(default=timezone.now)

class HolidaySync(models.Model):
    year=models.PositiveSmallIntegerField(unique=True)
    state=models.CharField(max_length=16,default='waiting')
    last_success=models.DateTimeField(null=True)
    last_attempt=models.DateTimeField(null=True)
    next_attempt=models.DateTimeField(default=timezone.now)
    lease_until=models.DateTimeField(default=timezone.now)
    token=models.CharField(max_length=64,blank=True)
    message=models.CharField(max_length=200,blank=True)

class LeaveCalendarReview(models.Model):
    request=models.OneToOneField('tasks.LeaveRequest',on_delete=models.PROTECT,related_name='calendar_review')
    pending=models.BooleanField(default=True)
    changed_dates=models.JSONField(default=list)
    updated_at=models.DateTimeField(default=timezone.now)

class LeaveEntry(models.Model):
    employee=models.ForeignKey('tasks.Employee',on_delete=models.PROTECT)
    year=models.PositiveSmallIntegerField()
    amount=models.DecimalField(max_digits=7,decimal_places=2)
    reason=models.CharField(max_length=500)
    actor=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT)
    created_at=models.DateTimeField(default=timezone.now)
    key=models.CharField(max_length=80,unique=True)
    class Meta: ordering=['-created_at','-pk']

class LeaveRequest(models.Model):
    employee=models.ForeignKey('tasks.Employee',on_delete=models.PROTECT,related_name='leave_requests')
    start=models.DateField('시작일')
    end=models.DateField('종료일')
    kind=models.CharField('휴가 종류',max_length=12,choices=[('annual','연차'),('am','오전 반차'),('pm','오후 반차')])
    days=models.DecimalField(max_digits=6,decimal_places=2)
    dates=models.JSONField(default=list)
    reason=models.TextField('신청 사유',max_length=1000)
    state=models.CharField(max_length=16,default='pending',choices=[('pending','결재 대기'),('approved','승인 완료'),('rejected','반려'),('withdrawn','회수'),('cancel_pending','취소 결재 대기'),('cancelled','취소 완료')])
    revision=models.PositiveIntegerField(default=0)
    created_at=models.DateTimeField(default=timezone.now)
    approved_at=models.DateTimeField(null=True)
    class Meta: ordering=['-created_at','-pk']

class LeaveApproval(models.Model):
    request=models.ForeignKey(LeaveRequest,on_delete=models.PROTECT,related_name='steps')
    round=models.PositiveSmallIntegerField(default=0)
    order=models.PositiveSmallIntegerField()
    role=models.CharField(max_length=12,choices=[('leader','팀장'),('head','본부장'),('executive','대표')])
    approver=models.ForeignKey('tasks.Employee',on_delete=models.PROTECT)
    approver_name=models.CharField(max_length=160)
    delegated=models.BooleanField(default=False)
    decision=models.CharField(max_length=12,blank=True)
    decided_at=models.DateTimeField(null=True)
    class Meta:
        ordering=['round','order']
        constraints=[models.UniqueConstraint(fields=['request','round','order'],name='unique_leave_step')]

class LeaveEvent(models.Model):
    request=models.ForeignKey(LeaveRequest,on_delete=models.PROTECT,related_name='events')
    actor=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT)
    actor_name=models.CharField(max_length=160)
    action=models.CharField(max_length=80)
    body=models.CharField(max_length=1000,blank=True)
    created_at=models.DateTimeField(default=timezone.now)
    class Meta: ordering=['-created_at','-pk']
