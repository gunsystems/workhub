from django.conf import settings
from django.db import models
from django.utils import timezone


class Proposal(models.Model):
    CATEGORIES=[('purchase','구매·비용 지출'),('education','교육·자격증 지원'),('benefit','복지 비용 지원'),('travel','출장·고객 업무 비용'),('contract','계약·갱신'),('other','기타 품의')]
    STATES=[('draft','임시 저장'),('pending','결재 대기'),('approved','승인 완료'),('rejected','반려'),('withdrawn','회수')]
    employee=models.ForeignKey('Employee',on_delete=models.PROTECT,related_name='proposals')
    title=models.CharField('제목',max_length=200)
    category=models.CharField('품의 구분',max_length=16,choices=CATEGORIES)
    stage=models.CharField('신청 목적',max_length=12,choices=[('prior','사용 전 승인 요청'),('claim','사용 후 비용 청구')],default='prior')
    body=models.TextField('요청 내용·필요 사유',max_length=10000)
    amount=models.DecimalField('총 요청 금액 · 원 (부가세 포함)',max_digits=14,decimal_places=0,default=0)
    line_items=models.JSONField(default=list,blank=True)
    needed_on=models.DateField('구매·지급 희망일',null=True,blank=True)
    supplier=models.CharField('구매처·교육기관·업체',max_length=160,blank=True)
    specifics=models.TextField('품목·수량 / 교육·지원 상세',max_length=3000,blank=True)
    task=models.ForeignKey('Task',on_delete=models.PROTECT,null=True,blank=True)
    original=models.ForeignKey('self',on_delete=models.PROTECT,null=True,blank=True,related_name='claims')
    state=models.CharField(max_length=16,choices=STATES,default='draft')
    revision=models.PositiveIntegerField(default=0)
    round=models.PositiveIntegerField(default=0)
    created_at=models.DateTimeField(default=timezone.now)
    submitted_at=models.DateTimeField(null=True)
    approved_at=models.DateTimeField(null=True)
    paid_at=models.DateTimeField(null=True)
    class Meta:ordering=['-created_at','-pk']


class ProposalStep(models.Model):
    proposal=models.ForeignKey(Proposal,on_delete=models.PROTECT,related_name='steps')
    round=models.PositiveIntegerField()
    order=models.PositiveSmallIntegerField()
    approver=models.ForeignKey('Employee',on_delete=models.PROTECT)
    name=models.CharField(max_length=160)
    decision=models.CharField(max_length=12,blank=True)
    decided_at=models.DateTimeField(null=True)
    class Meta:
        ordering=['round','order']
        constraints=[models.UniqueConstraint(fields=['proposal','round','order'],name='unique_proposal_step')]


class ProposalEvent(models.Model):
    proposal=models.ForeignKey(Proposal,on_delete=models.PROTECT,related_name='events')
    actor=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT)
    actor_name=models.CharField(max_length=160)
    action=models.CharField(max_length=40)
    body=models.CharField(max_length=1000,blank=True)
    snapshot=models.JSONField(default=dict)
    created_at=models.DateTimeField(default=timezone.now)
    class Meta:ordering=['-created_at','-pk']


class ProposalNotice(models.Model):
    event=models.ForeignKey(ProposalEvent,on_delete=models.PROTECT)
    user=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT)
    kind=models.CharField(max_length=12)
    email=models.EmailField()
    message=models.CharField(max_length=200)
    read_at=models.DateTimeField(null=True)
    mail_state=models.CharField(max_length=12,default='pending')
    created_at=models.DateTimeField(default=timezone.now)
    class Meta:
        ordering=['-created_at','-pk']
        constraints=[models.UniqueConstraint(fields=['event','user','kind'],name='unique_proposal_notice')]
