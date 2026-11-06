from django.db import models
from django.conf import settings
from django.utils import timezone
from datetime import timedelta
from django.core.validators import MinValueValidator, MaxValueValidator

class Department(models.Model):
    name = models.CharField('부서명', max_length=80, unique=True)
    def __str__(self): return self.name

class Employee(models.Model):
    ROLE = [('employee','일반 직원'),('manager','부서 관리자')]
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='employee', verbose_name='로그인 계정')
    role = models.CharField('업무 권한', max_length=10, choices=ROLE, default='employee')
    name = models.CharField('이름', max_length=80)
    department = models.ForeignKey(Department,on_delete=models.PROTECT,verbose_name='부서')
    def __str__(self): return f'{self.name} · {self.department}'

class Task(models.Model):
    STATUS = [('todo','예정'),('doing','진행 중'),('blocked','보류'),('review','검토 대기'),('done','완료')]
    IMPORTANCE = [('normal','보통'),('high','중요'),('critical','매우 중요')]
    PRIORITY = [(1,'P1 · 최우선'),(2,'P2 · 높음'),(3,'P3 · 일반'),(4,'P4 · 낮음')]
    title = models.CharField('업무명', max_length=160)
    description = models.TextField('업무 설명', blank=True)
    assignee = models.ForeignKey(Employee,on_delete=models.PROTECT,verbose_name='담당자')
    status = models.CharField('진행 상태',max_length=10,choices=STATUS,default='todo')
    importance = models.CharField('중요도',max_length=10,choices=IMPORTANCE,default='normal')
    priority = models.PositiveSmallIntegerField('우선순위',choices=PRIORITY,default=3)
    start_date = models.DateField('시작일')
    due_date = models.DateField('마감일')
    original_due_date = models.DateField('최초 마감일', null=True, editable=False)
    progress = models.PositiveSmallIntegerField('진행률', default=0, validators=[MaxValueValidator(100)])
    last_report_at = models.DateTimeField('최근 진행 보고', null=True, editable=False)
    submitted_at = models.DateTimeField('완료 보고일', null=True, editable=False)
    completed_at = models.DateTimeField('완료 기준일', null=True, editable=False)
    approved_at = models.DateTimeField('승인일', null=True, editable=False)
    quality_score = models.PositiveSmallIntegerField('결과 평가', null=True, editable=False, validators=[MinValueValidator(0), MaxValueValidator(100)])
    delay_reason = models.TextField('지연·장애 사유', blank=True)

    def save(self, *args, **kwargs):
        if self._state.adding and self.original_due_date is None:
            self.original_due_date = self.due_date
        super().save(*args, **kwargs)

    @property
    def needs_report(self):
        today = timezone.localdate()
        last = timezone.localtime(self.last_report_at).date() if self.last_report_at else self.start_date
        return self.status in ['todo','doing','blocked'] and self.start_date <= today and (today-last).days >= 3

    @property
    def not_started(self):
        return self.status == 'todo' and self.start_date < timezone.localdate()

    class Meta:
        ordering = ['priority','due_date','pk']
        constraints = [models.CheckConstraint(condition=models.Q(due_date__gte=models.F('start_date')),name='valid_task_dates')]
    @property
    def deadline(self):
        if self.status == 'done': return 'done'
        if self.status == 'review': return 'review'
        today = timezone.localdate()
        if self.due_date < today: return 'overdue'
        if self.due_date <= today + timedelta(days=3): return 'soon'
        return 'normal'
    def __str__(self): return self.title

class TaskEvent(models.Model):
    KIND = [('created','업무 등록'),('changed','업무 변경'),('progress','진행 보고'),('submitted','완료 보고'),('approved','완료 승인'),('returned','보완 요청'),('feedback','관리자 피드백')]
    task = models.ForeignKey(Task, on_delete=models.CASCADE, related_name='events')
    author = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True)
    author_name = models.CharField(max_length=150)
    kind = models.CharField(max_length=12, choices=KIND)
    body = models.TextField()
    snapshot = models.JSONField(default=dict)
    created_at = models.DateTimeField(default=timezone.now)
    class Meta:
        ordering = ['-created_at','-pk']


class EmployeeFeedback(models.Model):
    employee = models.ForeignKey(Employee, on_delete=models.PROTECT, related_name='feedbacks')
    author = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True)
    author_name = models.CharField(max_length=150)
    body = models.TextField('종합 피드백 · 개선 계획')
    created_at = models.DateTimeField(default=timezone.now)
    class Meta:
        ordering = ['-created_at','-pk']
