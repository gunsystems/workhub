from django.db import models
from django.conf import settings
from django.utils import timezone
from datetime import timedelta

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
    STATUS = [('todo','예정'),('doing','진행 중'),('blocked','보류'),('done','완료')]
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
    class Meta:
        ordering = ['priority','due_date','pk']
        constraints = [models.CheckConstraint(condition=models.Q(due_date__gte=models.F('start_date')),name='valid_task_dates')]
    @property
    def deadline(self):
        if self.status == 'done': return 'done'
        today = timezone.localdate()
        if self.due_date < today: return 'overdue'
        if self.due_date <= today + timedelta(days=3): return 'soon'
        return 'normal'
    def __str__(self): return self.title
