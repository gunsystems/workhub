"""Surveys and executive assessments use separate permissions and records."""
from django.conf import settings
from django.db import models
from django.utils import timezone


class Survey(models.Model):
    title=models.CharField('설문 제목',max_length=160)
    description=models.TextField('설문 안내',blank=True)
    creator=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT)
    state=models.CharField(max_length=12,default='draft',choices=[('draft','작성 중'),('open','진행 중'),('closed','마감')])
    anonymous=models.BooleanField('익명 응답',default=False)
    audience=models.CharField('대상',max_length=12,choices=[('all','전사'),('team','부서 선택'),('person','직원 선택')],default='all')
    departments=models.ManyToManyField('tasks.Department',blank=True)
    employees=models.ManyToManyField('tasks.Employee',blank=True)
    starts_at=models.DateTimeField('응답 시작',default=timezone.now)
    closes_at=models.DateTimeField('응답 마감')
    send_email=models.BooleanField('이메일 알림',default=True)
    send_messenger=models.BooleanField('하이웍스 알림',default=False)
    reminders=models.BooleanField('마감 3일 전·1일 전 미응답자에게 재알림',default=True)
    created_at=models.DateTimeField(default=timezone.now)
    class Meta: ordering=['-pk']


class SurveyQuestion(models.Model):
    survey=models.ForeignKey(Survey,on_delete=models.CASCADE,related_name='questions')
    order=models.PositiveIntegerField(default=0)
    label=models.CharField('질문',max_length=300)
    kind=models.CharField('응답 방식',max_length=12,choices=[('single','하나 선택'),('multiple','여러 개 선택'),('text','자유 의견'),('rating','만족도 1~5')])
    options=models.TextField('선택지 · 한 줄에 하나',blank=True)
    required=models.BooleanField('필수 응답',default=True)
    class Meta: ordering=['order','pk']


class SurveyParticipation(models.Model):
    survey=models.ForeignKey(Survey,on_delete=models.PROTECT,related_name='participations')
    employee=models.ForeignKey('tasks.Employee',on_delete=models.PROTECT)
    submitted=models.BooleanField(default=False)
    draft=models.JSONField(default=dict)
    class Meta:
        constraints=[models.UniqueConstraint(fields=['survey','employee'],name='unique_survey_participant')]


class SurveySubmission(models.Model):
    survey=models.ForeignKey(Survey,on_delete=models.PROTECT,related_name='submissions')
    # Anonymous records have no participation/user/employee key or individual timestamp.
    employee=models.ForeignKey('tasks.Employee',on_delete=models.PROTECT,null=True)
    answers=models.JSONField(default=dict)
    class Meta:
        constraints=[models.UniqueConstraint(fields=['survey','employee'],name='unique_named_survey_submission')]


class SurveyDelivery(models.Model):
    participation=models.ForeignKey(SurveyParticipation,on_delete=models.PROTECT,related_name='deliveries')
    channel=models.CharField(max_length=12)
    stage=models.CharField(max_length=12)
    state=models.CharField(max_length=12,default='pending')
    attempted_at=models.DateTimeField(null=True)
    result=models.CharField(max_length=200,blank=True)
    class Meta:
        constraints=[models.UniqueConstraint(fields=['participation','channel','stage'],name='unique_survey_delivery')]


class StaffAssessment(models.Model):
    employee=models.ForeignKey('tasks.Employee',on_delete=models.PROTECT,related_name='assessments')
    task=models.ForeignKey('tasks.Task',on_delete=models.PROTECT,null=True,blank=True)
    author=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT)
    period_start=models.DateField('평가 시작일')
    period_end=models.DateField('평가 종료일')
    scores=models.JSONField(default=dict)
    evidence=models.TextField('관찰·평가 근거')
    context=models.TextField('업무 난이도·업무량·외부 사정',blank=True)
    state=models.CharField(max_length=12,default='draft',choices=[('draft','작성 중'),('final','확정')])
    revision=models.PositiveIntegerField(default=0)
    updated_at=models.DateTimeField(default=timezone.now)


class AssessmentRevision(models.Model):
    assessment=models.ForeignKey(StaffAssessment,on_delete=models.PROTECT,related_name='history')
    actor=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT)
    revision=models.PositiveIntegerField()
    snapshot=models.JSONField()
    reason=models.CharField(max_length=300)
    created_at=models.DateTimeField(default=timezone.now)
    class Meta:
        constraints=[models.UniqueConstraint(fields=['assessment','revision'],name='unique_assessment_revision')]
