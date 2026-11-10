from django.db import models,transaction
from django.conf import settings
from django.utils import timezone
from datetime import timedelta
from django.core.validators import MinValueValidator, MaxValueValidator
from .notice_models import Notice, NoticeRecipient, NoticeDelivery


class SystemAudit(models.Model):
    """Application history survives deletion of the original object and account."""
    created_at = models.DateTimeField(default=timezone.now, db_index=True)
    actor_id_snapshot = models.PositiveBigIntegerField(null=True)
    actor_name = models.CharField(max_length=150, default='시스템')
    action = models.CharField(max_length=24, db_index=True)
    target_type = models.CharField(max_length=80, db_index=True)
    target_id = models.CharField(max_length=80, blank=True)
    target_label = models.CharField(max_length=200, blank=True)
    before = models.JSONField(default=dict)
    after = models.JSONField(default=dict)
    class Meta:
        ordering = ['-created_at', '-pk']


class AssignmentMail(models.Model):
    task = models.ForeignKey('Task', on_delete=models.SET_NULL, null=True)
    employee = models.ForeignKey('Employee', on_delete=models.SET_NULL, null=True)
    dedupe_key = models.CharField(max_length=120, unique=True)
    payload = models.JSONField(default=dict)
    state = models.CharField(max_length=16, default='pending', db_index=True,
        choices=[('pending','발송 대기'),('sending','발송 처리 중'),('sent','발송 완료'),('failed','발송 실패'),('skipped','발송 제외'),('unknown','결과 확인 필요')])
    attempts = models.PositiveSmallIntegerField(default=0)
    next_attempt_at = models.DateTimeField(default=timezone.now)
    claimed_until = models.DateTimeField(null=True)
    created_at = models.DateTimeField(default=timezone.now)
    sent_at = models.DateTimeField(null=True)
    result = models.CharField(max_length=160, blank=True)

class Department(models.Model):
    name = models.CharField('부서명', max_length=80, unique=True)
    def __str__(self): return self.name

class Employee(models.Model):
    supervisor_auto = models.BooleanField('직속 상위자 자동 연결',default=True,editable=False)
    POSITION = [('member','팀원'),('leader','팀장'),('head','본부장'),('executive','대표')]
    position = models.CharField('직책', max_length=12, choices=POSITION, default='member')
    supervisor = models.ForeignKey('self', verbose_name='직속 상위자', on_delete=models.SET_NULL, null=True, blank=True)
    ROLE = [('employee','일반 직원'),('manager','부서 관리자')]
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='employee', verbose_name='로그인 계정')
    role = models.CharField('업무 권한', max_length=10, choices=ROLE, default='employee')
    name = models.CharField('이름', max_length=80)
    is_active = models.BooleanField('재직 · 사용 허용', default=True)
    department = models.ForeignKey(Department,on_delete=models.PROTECT,verbose_name='부서')
    def __str__(self): return f'{self.name} · {self.department}'

    def save(self,*args,**kwargs):
        fields=kwargs.get('update_fields')
        if fields is None or 'supervisor' in fields or 'supervisor_id' in fields:
            previous=type(self).objects.filter(pk=self.pk).values('supervisor_id').first() if self.pk else None
            if (previous and previous['supervisor_id']!=self.supervisor_id) or (self._state.adding and self.supervisor_id):
                self.supervisor_auto=self.supervisor_id is None
                if fields is not None:kwargs['update_fields']=set(fields)|{'supervisor_auto'}
        with transaction.atomic():
            super().save(*args,**kwargs)

    @property
    def business_role_display(self):
        if self.position in ['head','executive']:
            return '임원 · 전사 업무 관리'
        return '부서 관리자 · 소속 부서 업무 관리' if self.role=='manager' else '일반 직원'

    def clean(self):
        from django.core.exceptions import ValidationError
        seen={self.pk} if self.pk else set()
        node=self.supervisor
        while node:
            if node.pk in seen: raise ValidationError({'supervisor':'직속 상위자에 본인이나 순환 관계를 지정할 수 없습니다.'})
            seen.add(node.pk); node=node.supervisor

class Project(models.Model):
    name = models.CharField('프로젝트명', max_length=160)
    department = models.ForeignKey(Department, on_delete=models.PROTECT, verbose_name='담당 부서')
    owner = models.ForeignKey(Employee, on_delete=models.PROTECT, verbose_name='총괄 담당자')
    description = models.TextField('목표 · 완료 기준', blank=True)
    start_date = models.DateField('시작일')
    due_date = models.DateField('목표 마감일')
    created_at = models.DateTimeField(default=timezone.now)
    class Meta:
        ordering = ['due_date','pk']
        constraints = [models.CheckConstraint(condition=models.Q(due_date__gte=models.F('start_date')),name='valid_project_dates')]
    def __str__(self): return self.name


class WorkCategory(models.Model):
    name = models.CharField('업무 종류', max_length=40, unique=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    is_active = models.BooleanField(default=True)
    class Meta:
        ordering = ['pk']
    def __str__(self): return self.name


class Task(models.Model):
    assignment_email = models.BooleanField('이메일로 배정 알림',default=True)
    assignment_messenger = models.BooleanField('하이웍스 메신저로 배정 알림',default=True)
    start_time = models.TimeField('시작 시간', null=True, blank=True)
    category = models.ForeignKey(WorkCategory, on_delete=models.PROTECT, null=True, blank=True, verbose_name='업무 종류')
    management_mode = models.CharField('관리 방식', max_length=12, default='report', choices=[('report','보고 업무'),('event','간단 일정')])
    activity_start = models.DateTimeField('실제 수행 시작', null=True, blank=True)
    activity_end = models.DateTimeField('실제 수행 종료', null=True, blank=True)
    location = models.CharField('장소 · 접속 안내', max_length=200, blank=True)
    customer = models.CharField('고객사 · 거래처', max_length=120, blank=True)
    work_style = models.CharField('근무 형태', max_length=12, blank=True, choices=[('','미지정'),('office','내근'),('outside','외근'),('online','온라인')])
    estimated_minutes = models.PositiveIntegerField('예상 소요시간 (분)', null=True, blank=True)
    next_action = models.CharField('다음 조치', max_length=240, blank=True)
    next_check_date = models.DateField('다음 확인일', null=True, blank=True)
    STATUS = [('draft','초안'),('todo','예정'),('doing','진행 중'),('blocked','보류'),('review','검토 대기'),('done','완료'),('cancelled','취소')]
    workflow_version = models.PositiveSmallIntegerField(default=1, editable=False)
    revision = models.PositiveIntegerField(default=0, editable=False)
    report_round = models.PositiveIntegerField(default=0, editable=False)
    review_started_at = models.DateTimeField(null=True, editable=False)
    creator = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='created_tasks')
    participants = models.ManyToManyField(Employee, through='TaskParticipation', related_name='collaborating_tasks')
    watchers = models.ManyToManyField(settings.AUTH_USER_MODEL, blank=True, related_name='watched_tasks')
    group_key = models.CharField(max_length=32, blank=True, editable=False)
    IMPORTANCE = [('normal','보통'),('high','중요'),('critical','매우 중요')]
    PRIORITY = [(1,'P1 · 최우선'),(2,'P2 · 높음'),(3,'P3 · 일반'),(4,'P4 · 낮음')]
    title = models.CharField('업무명', max_length=160)
    description = models.TextField('업무 설명', blank=True)
    project = models.ForeignKey(Project, on_delete=models.PROTECT, null=True, blank=True, related_name='tasks', verbose_name='프로젝트')
    completion_criteria = models.TextField('완료 조건 · 필수 결과물', blank=True)
    assigned_at = models.DateTimeField('최근 배정일', null=True, editable=False)
    accepted_at = models.DateTimeField('배정 확인일', null=True, editable=False)
    report_days = models.PositiveSmallIntegerField('진행 보고 주기 (일)', default=3, validators=[MinValueValidator(1),MaxValueValidator(30)])
    review_days = models.PositiveSmallIntegerField('완료 검토 기한 (일)', default=2, validators=[MinValueValidator(1),MaxValueValidator(30)])
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
        if self._state.adding and self.assigned_at is None:
            self.assigned_at = timezone.now()
        if self._state.adding and self.original_due_date is None:
            self.original_due_date = self.due_date
        super().save(*args, **kwargs)

    @property
    def needs_report(self):
        if self.management_mode == 'event': return False
        today = timezone.localdate()
        last = timezone.localtime(self.last_report_at).date() if self.last_report_at else self.start_date
        if self.assigned_at: last = max(last, timezone.localtime(self.assigned_at).date())
        return self.status in ['todo','doing','blocked'] and self.start_date <= today and (today-last).days >= self.report_days

    @property
    def awaiting_acceptance(self):
        return self.accepted_at is None and self.status in ['todo','doing','blocked']

    @property
    def review_overdue(self):
        started = self.review_started_at or self.submitted_at
        return self.status == 'review' and started is not None and (timezone.localdate()-timezone.localtime(started).date()).days >= self.review_days

    @property
    def not_started(self):
        return self.status == 'todo' and self.start_date < timezone.localdate()

    class Meta:
        ordering = ['priority','due_date','pk']
        constraints = [models.CheckConstraint(condition=models.Q(due_date__gte=models.F('start_date')),name='valid_task_dates')]
    @property
    def deadline(self):
        if self.status in ['draft','cancelled']: return 'inactive'
        if self.status == 'done': return 'done'
        if self.status == 'review': return 'review'
        today = timezone.localdate()
        if self.due_date < today: return 'overdue'
        if self.due_date <= today + timedelta(days=3): return 'soon'
        return 'normal'
    def __str__(self): return self.title

class TaskEvent(models.Model):
    KIND = [('accepted','배정 확인'),('checklist','체크리스트'),('created','업무 등록'),('changed','업무 변경'),('progress','진행 보고'),('submitted','완료 보고'),('approved','완료 승인'),('returned','보완 요청'),('feedback','피드백'),('comment','질문 · 의견'),('override','대표 직권 처리'),('handover','업무 인계')]
    task = models.ForeignKey(Task, on_delete=models.CASCADE, related_name='events')
    author = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True)
    author_name = models.CharField(max_length=150)
    kind = models.CharField(max_length=12, choices=KIND)
    body = models.TextField()
    snapshot = models.JSONField(default=dict)
    created_at = models.DateTimeField(default=timezone.now)
    class Meta:
        ordering = ['-created_at','-pk']


class ScheduleRegistration(models.Model):
    task=models.OneToOneField(Task,on_delete=models.CASCADE,related_name='schedule_registration')
    registered_at=models.DateTimeField(default=timezone.now)
    initial_plan=models.JSONField(default=dict)
    late=models.BooleanField(default=False)


class ScheduleOutcome(models.Model):
    task=models.ForeignKey(Task,on_delete=models.CASCADE,related_name='schedule_outcomes')
    employee=models.ForeignKey(Employee,on_delete=models.PROTECT)
    result=models.CharField(max_length=15,choices=[('attended','참석 · 실시 완료'),('absent','불참 · 미실시'),('postponed','연기'),('explanation','등록·변경 사유 설명')])
    actual_date=models.DateField(null=True,blank=True)
    reason=models.TextField()
    updated_at=models.DateTimeField(auto_now=True)
    reviewer=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.SET_NULL,null=True,blank=True)
    review_note=models.TextField(blank=True)
    reviewed_at=models.DateTimeField(null=True,blank=True)
    class Meta:
        constraints=[models.UniqueConstraint(fields=['task','employee'],name='one_schedule_outcome_per_employee')]


class ScheduleExpectation(models.Model):
    title=models.CharField(max_length=160)
    employee=models.ForeignKey(Employee,on_delete=models.PROTECT)
    creator=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.SET_NULL,null=True)
    register_by=models.DateTimeField()
    expected_date=models.DateField()
    task=models.ForeignKey(Task,on_delete=models.SET_NULL,null=True,blank=True)
    closed_reason=models.TextField(blank=True)
    created_at=models.DateTimeField(default=timezone.now)
    class Meta:
        ordering=['register_by','pk']


class DeadlinePolicy(models.Model):
    enabled=models.BooleanField(default=False)
    recipient_employee=models.ForeignKey(Employee,on_delete=models.PROTECT,null=True,blank=True)
    advance_days=models.PositiveSmallIntegerField(default=3,validators=[MinValueValidator(1),MaxValueValidator(30)])
    send_hour=models.PositiveSmallIntegerField(default=9,validators=[MaxValueValidator(23)])
    escalation_days=models.PositiveSmallIntegerField(default=3,validators=[MinValueValidator(1),MaxValueValidator(30)])
    send_email=models.BooleanField(default=False)
    send_messenger=models.BooleanField(default=False)
    weekdays_only=models.BooleanField(default=True)
    activated_at=models.DateTimeField(null=True,blank=True)


class DeadlineDelivery(models.Model):
    task=models.ForeignKey(Task,on_delete=models.CASCADE)
    user=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.CASCADE)
    dedupe_key=models.CharField(max_length=64,unique=True)
    kind=models.CharField(max_length=20)
    phase=models.CharField(max_length=12)
    due_date=models.DateField()
    alert_date=models.DateField()
    baseline=models.CharField(max_length=100)
    message=models.CharField(max_length=240)
    notification=models.ForeignKey('WorkNotification',on_delete=models.SET_NULL,null=True)
    email_state=models.CharField(max_length=12,default='off')
    messenger_state=models.CharField(max_length=12,default='off')
    created_at=models.DateTimeField(default=timezone.now)
    sent_at=models.DateTimeField(null=True,blank=True)
    @property
    def email_label(self):
        return {'off':'사용 안 함','pending':'발송 대기','sending':'발송 처리 중','sent':'발송 서버 접수','unknown':'결과 확인 필요','skipped':'발송 제외'}.get(self.email_state,self.email_state)
    @property
    def messenger_label(self):
        return {'off':'사용 안 함','pending':'발송 대기','queued':'메신저 전송 대기열 접수','skipped':'발송 제외 · 연결 설정 확인'}.get(self.messenger_state,self.messenger_state)


class EmployeeFeedback(models.Model):
    employee = models.ForeignKey(Employee, on_delete=models.PROTECT, related_name='feedbacks')
    author = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True)
    author_name = models.CharField(max_length=150)
    body = models.TextField('종합 피드백 · 개선 계획')
    created_at = models.DateTimeField(default=timezone.now)
    class Meta:
        ordering = ['-created_at','-pk']


class WorkRequest(models.Model):
    title = models.CharField('요청명', max_length=160)
    description = models.TextField('요청 내용 · 완료 조건')
    department = models.ForeignKey(Department, on_delete=models.PROTECT, verbose_name='처리 부서')
    project = models.ForeignKey(Project, on_delete=models.PROTECT, null=True, blank=True, verbose_name='프로젝트')
    requested_due_date = models.DateField('희망 마감일')
    creator = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True)
    creator_name = models.CharField(max_length=150)
    created_at = models.DateTimeField(default=timezone.now)
    task = models.OneToOneField(Task, on_delete=models.PROTECT, null=True, blank=True, related_name='intake')
    assigned_at = models.DateTimeField('요청 배정일', null=True, editable=False)
    closed_at = models.DateTimeField(null=True)
    close_reason = models.TextField(blank=True)
    class Meta:
        ordering = ['created_at','pk']
    @property
    def state(self):
        return '배정 완료' if self.task_id else ('취소' if self.closed_at else '배정 대기')
    @property
    def overdue(self):
        return not self.task_id and not self.closed_at and self.requested_due_date < timezone.localdate()


class ChecklistItem(models.Model):
    task = models.ForeignKey(Task, on_delete=models.CASCADE, related_name='checklist')
    title = models.CharField('확인 항목', max_length=200)
    required = models.BooleanField('필수 항목', default=True)
    checked = models.BooleanField(default=False)
    retired = models.BooleanField(default=False)
    checked_at = models.DateTimeField(null=True)
    class Meta:
        ordering = ['pk']


class TaskParticipation(models.Model):
    task = models.ForeignKey(Task, on_delete=models.CASCADE, related_name='assignments')
    employee = models.ForeignKey(Employee, on_delete=models.PROTECT)
    accepted_at = models.DateTimeField(null=True)
    progress = models.PositiveSmallIntegerField(default=0, validators=[MaxValueValidator(100)])
    completed_at = models.DateTimeField(null=True)
    class Meta:
        constraints = [models.UniqueConstraint(fields=['task','employee'], name='unique_task_participant')]


class ApprovalStep(models.Model):
    task = models.ForeignKey(Task, on_delete=models.CASCADE, related_name='approval_steps')
    approver = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True)
    approver_name = models.CharField(max_length=150)
    position = models.PositiveSmallIntegerField()
    stage = models.PositiveSmallIntegerField(default=1)
    decision = models.CharField(max_length=12, default='pending', choices=[('pending','대기'),('approved','승인'),('returned','반려')])
    decided_at = models.DateTimeField(null=True)
    class Meta:
        ordering = ['stage','position','pk']
        constraints = [models.UniqueConstraint(fields=['task','position'], name='unique_task_approval_position')]


class PersonalBoard(models.Model):
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    name = models.CharField('보드 이름', max_length=80)
    class Meta:
        ordering = ['pk']
    def __str__(self): return self.name


class PersonalColumn(models.Model):
    COLORS=[('slate','회청색'),('blue','파란색'),('green','녹색'),('amber','주황색'),('purple','보라색'),('rose','분홍색')]
    ROLES=[('free','상태 연결 안 함'),('todo','할 일'),('doing','진행 중'),('done','완료')]
    board = models.ForeignKey(PersonalBoard, on_delete=models.CASCADE, related_name='columns')
    name = models.CharField('목록 이름', max_length=80)
    color = models.CharField('목록 색상',max_length=12,choices=COLORS,default='slate')
    workflow_role = models.CharField('연결할 상태',max_length=12,choices=ROLES,default='free')
    position = models.PositiveIntegerField(default=0)
    class Meta:
        ordering = ['position','pk']
    def __str__(self): return self.name


class PersonalCard(models.Model):
    PRIORITIES=[('urgent','긴급'),('high','중요'),('normal','보통'),('low','낮음')]
    column = models.ForeignKey(PersonalColumn, on_delete=models.CASCADE, related_name='cards')
    title = models.CharField('제목', max_length=160)
    body = models.TextField('메모 · 링크 · 수행 기록', blank=True)
    checklist = models.JSONField(default=list)
    label = models.CharField('라벨', max_length=40, blank=True)
    priority = models.CharField('중요도',max_length=12,choices=PRIORITIES,default='normal')
    due_date = models.DateField('기한 · 알림일', null=True, blank=True)
    repeat = models.CharField('반복', max_length=12, default='', blank=True, choices=[('','반복 안 함'),('daily','매일'),('weekly','매주'),('monthly','매월')])
    repeat_day = models.PositiveSmallIntegerField(null=True,editable=False)
    state = models.CharField(max_length=12, default='active', choices=[('active','진행'),('done','완료'),('archived','보관'),('trash','휴지통')])
    position = models.PositiveIntegerField(default=0)
    completed_at = models.DateTimeField(null=True)
    updated_at = models.DateTimeField(auto_now=True)
    next_occurrence = models.OneToOneField('self', on_delete=models.SET_NULL, null=True, blank=True, related_name='previous_occurrence')
    tags = models.ManyToManyField('PersonalTag',blank=True,related_name='cards')
    due_time = models.TimeField('예정 시간 · 한국 시간',null=True,blank=True)
    reminder_minutes = models.PositiveIntegerField('미리 알림 (분)',default=0)
    reminder_enabled = models.BooleanField('프로그램 내 알림 받기',default=True)
    calendar_display = models.CharField('Google Calendar 표시',max_length=12,default='none',choices=[('none','연동하지 않음'),('minimal','시간만 · 제목은 업무 일정'),('title','제목 · 시간 · 바로가기'),('details','제목 · 시간 · 메모 포함')])
    series = models.ForeignKey('PersonalSchedule',null=True,blank=True,on_delete=models.SET_NULL,related_name='occurrences')
    occurrence_at = models.DateTimeField(null=True,blank=True)
    class Meta:
        ordering = ['position','pk']
        constraints=[models.UniqueConstraint(fields=['series','occurrence_at'],name='unique_personal_schedule_occurrence')]

    @property
    def checked_count(self): return sum(bool(item.get('done')) for item in self.checklist)

    @property
    def display_status(self):
        if self.state!='active': return self.get_state_display()
        return {'todo':'할 일','doing':'진행 중','done':'완료 확인 필요','free':'진행 카드'}[self.column.workflow_role]


class WorkNotification(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    task = models.ForeignKey(Task, on_delete=models.CASCADE)
    message = models.CharField(max_length=240)
    created_at = models.DateTimeField(default=timezone.now)
    read_at = models.DateTimeField(null=True)
    class Meta:
        ordering = ['-created_at','-pk']


class PersonalTag(models.Model):
    owner=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.CASCADE,null=True,blank=True)
    name=models.CharField('태그 이름',max_length=40)
    class Meta:
        ordering=['owner_id','name']
        constraints=[models.UniqueConstraint(fields=['owner','name'],name='unique_owner_tag'),
                     models.UniqueConstraint(fields=['name'],condition=models.Q(owner__isnull=True),name='unique_default_tag')]
    def __str__(self): return self.name


class PersonalSchedule(models.Model):
    template_card=models.OneToOneField(PersonalCard,on_delete=models.CASCADE,related_name='schedule_template')
    column=models.ForeignKey(PersonalColumn,on_delete=models.CASCADE)
    mode=models.CharField(max_length=12,default='calendar',choices=[('calendar','일정 기준'),('completion','완료 기준')])
    kind=models.CharField(max_length=20,default='daily')
    interval=models.PositiveSmallIntegerField(default=1)
    weekdays=models.JSONField(default=list)
    month_day=models.PositiveSmallIntegerField(default=1)
    week_number=models.SmallIntegerField(default=1)
    month_end=models.CharField(max_length=8,default='last')
    starts_at=models.DateTimeField()
    next_at=models.DateTimeField(null=True)
    until=models.DateField(null=True,blank=True)
    max_count=models.PositiveIntegerField(null=True,blank=True)
    generated_count=models.PositiveIntegerField(default=1)
    enabled=models.BooleanField(default=True)
    rule_version=models.PositiveIntegerField(default=1)

    @property
    def summary(self):
        labels={'daily':'일','weekdays':'주 · 평일','weekly':'주','monthly_date':'개월','monthly_weekday':'개월 · 요일 기준','yearly':'년'}
        text=f'{self.interval}{labels.get(self.kind,"")}마다'
        return text+(' · 완료 기준' if self.mode=='completion' else '')+(' · 중지됨' if not self.enabled else '')


class PersonalReminder(models.Model):
    card=models.ForeignKey(PersonalCard,on_delete=models.CASCADE,related_name='reminders')
    scheduled_for=models.DateTimeField()
    notify_at=models.DateTimeField()
    delivered_at=models.DateTimeField(null=True)
    read_at=models.DateTimeField(null=True)
    class Meta:
        constraints=[models.UniqueConstraint(fields=['card','scheduled_for'],name='unique_card_reminder')]
        ordering=['-notify_at']


class SchedulerLease(models.Model):
    key=models.CharField(max_length=32,primary_key=True)
    token=models.CharField(max_length=64,blank=True)
    expires_at=models.DateTimeField()
    heartbeat_at=models.DateTimeField(null=True)


class GoogleCalendarConnection(models.Model):
    user=models.OneToOneField(settings.AUTH_USER_MODEL,on_delete=models.CASCADE)
    subject=models.CharField(max_length=255)
    email=models.EmailField()
    encrypted_refresh_token=models.TextField()
    calendar_id=models.CharField(max_length=255)
    connected_at=models.DateTimeField(default=timezone.now)
    status=models.CharField(max_length=16,default='connected')
    last_sync_at=models.DateTimeField(null=True)
    error=models.CharField(max_length=240,blank=True)


class CalendarExportPolicy(models.Model):
    allow_official=models.BooleanField(default=False)


class CalendarExport(models.Model):
    user=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.CASCADE)
    card=models.OneToOneField(PersonalCard,null=True,blank=True,on_delete=models.CASCADE)
    task=models.ForeignKey(Task,null=True,blank=True,on_delete=models.CASCADE)
    detail=models.CharField(max_length=12,default='minimal')
    date_mode=models.CharField(max_length=12,default='auto')
    event_id=models.CharField(max_length=100,blank=True)
    calendar_id=models.CharField(max_length=255,blank=True)
    payload_digest=models.CharField(max_length=64,blank=True)
    error=models.CharField(max_length=240,blank=True)
    updated_at=models.DateTimeField(auto_now=True)
    class Meta:
        constraints=[models.CheckConstraint(condition=(models.Q(card__isnull=False,task__isnull=True)|models.Q(card__isnull=True,task__isnull=False)),name='calendar_export_one_source'),
                     models.UniqueConstraint(fields=['user','task'],name='unique_user_task_export')]


class HiworksLink(models.Model):
    auto_configured = models.BooleanField(default=False)
    employee = models.OneToOneField(Employee, on_delete=models.CASCADE)
    user_id = models.CharField(max_length=150, unique=True)
    remote_id = models.CharField(max_length=150, unique=True)
    email = models.EmailField()
    notifications_enabled = models.BooleanField(default=False)


class HiworksReview(models.Model):
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    rows = models.JSONField(default=list)
    created_at = models.DateTimeField(default=timezone.now)
    applied_at = models.DateTimeField(null=True)


class HiworksDelivery(models.Model):
    notification = models.OneToOneField(WorkNotification, on_delete=models.CASCADE)
    state = models.CharField(max_length=16, default='pending')
    created_at = models.DateTimeField(default=timezone.now)
    attempted_at = models.DateTimeField(null=True)
    detail = models.CharField(max_length=200, blank=True)


class HiworksSyncStatus(models.Model):
    key = models.CharField(max_length=32, primary_key=True, default='directory')
    checked_at = models.DateTimeField(null=True)
    status = models.CharField(max_length=16, default='pending')
    message = models.CharField(max_length=240, blank=True)


class HiworksDirectoryCache(models.Model):
    key = models.CharField(max_length=32, primary_key=True, default='access')
    rows = models.JSONField(default=list)
    checked_at = models.DateTimeField(null=True)
    attempted_at = models.DateTimeField(null=True)
    error = models.CharField(max_length=240, blank=True)


class WorkSeries(models.Model):
    template_task = models.ForeignKey(Task, on_delete=models.PROTECT, related_name='repeat_templates')
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True)
    template = models.JSONField(default=dict)
    mode = models.CharField(max_length=12, default='calendar')
    kind = models.CharField(max_length=20)
    interval = models.PositiveSmallIntegerField(default=1)
    weekdays = models.JSONField(default=list)
    month_day = models.PositiveSmallIntegerField(default=1)
    week_number = models.SmallIntegerField(default=1)
    month_end = models.CharField(max_length=8, default='last')
    starts_at = models.DateTimeField()
    next_at = models.DateTimeField(null=True)
    until = models.DateField(null=True, blank=True)
    max_count = models.PositiveIntegerField(null=True, blank=True)
    generated_count = models.PositiveIntegerField(default=1)
    enabled = models.BooleanField(default=True)
    issue = models.CharField(max_length=200, blank=True)
    created_at = models.DateTimeField(default=timezone.now)


class WorkOccurrence(models.Model):
    series = models.ForeignKey(WorkSeries, on_delete=models.PROTECT, related_name='occurrences')
    task = models.OneToOneField(Task, on_delete=models.PROTECT, related_name='repeat_occurrence')
    scheduled_for = models.DateTimeField()
    class Meta:
        constraints = [models.UniqueConstraint(fields=['series','scheduled_for'], name='unique_work_series_occurrence')]


class CalendarSubscription(models.Model):
    owner = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    token_digest = models.CharField(max_length=64, unique=True)
    enabled = models.BooleanField(default=True)
    created_at = models.DateTimeField(default=timezone.now)


class PeriodReport(models.Model):
    REPORT_TYPES=[('daily','일일 보고'),('weekly','주간 보고'),('monthly','월간 보고'),('individual','개별 보고'),('period','기간 보고')]
    report_type=models.CharField('보고 구분',max_length=12,choices=REPORT_TYPES,default='period')
    selected_tasks = models.ManyToManyField(Task, blank=True, related_name='period_reports')
    author = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name='period_reports')
    period_start = models.DateField('보고 시작일')
    period_end = models.DateField('보고 종료일')
    summary = models.TextField('수행 결과 · 장애 사항 · 다음 계획')
    reviewer = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name='period_reviews')
    state = models.CharField(max_length=12, default='draft', choices=[('draft','작성 중'),('submitted','검토 대기'),('returned','보완 요청'),('approved','승인')])
    revision = models.PositiveIntegerField(default=0)
    submitted_at = models.DateTimeField(null=True)
    current_submission = models.ForeignKey('ReportSubmission', on_delete=models.PROTECT, null=True, related_name='+')
    source_submissions = models.ManyToManyField('ReportSubmission', blank=True, related_name='aggregated_reports')


class ReportSubmission(models.Model):
    report = models.ForeignKey(PeriodReport, on_delete=models.PROTECT, related_name='submissions')
    version = models.PositiveIntegerField()
    snapshot = models.JSONField(default=dict)
    created_at = models.DateTimeField(default=timezone.now)
    class Meta:
        constraints = [models.UniqueConstraint(fields=['report','version'], name='unique_report_submission_version')]


class ReportReview(models.Model):
    submission = models.ForeignKey(ReportSubmission, on_delete=models.PROTECT, related_name='reviews')
    author = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True)
    decision = models.CharField(max_length=12)
    body = models.TextField()
    created_at = models.DateTimeField(default=timezone.now)


def private_report_path(instance, filename):
    import secrets
    from pathlib import Path
    return f'reports/{instance.report_id}/{secrets.token_hex(24)}{Path(filename).suffix.lower()}'


class ReportFile(models.Model):
    report = models.ForeignKey(PeriodReport, on_delete=models.PROTECT, related_name='files')
    file = models.FileField(upload_to=private_report_path)
    name = models.CharField(max_length=200)
    uploaded_at = models.DateTimeField(default=timezone.now)
