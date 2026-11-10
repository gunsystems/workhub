"""Allowlisted business audit. Authentication secrets and private memo contents are excluded."""
from contextvars import ContextVar
from django.apps import apps
from django.db.models.signals import pre_save, post_save, pre_delete, m2m_changed
from django.contrib.auth.signals import user_logged_in, user_logged_out
from .models import SystemAudit

_actor = ContextVar('workhub_audit_actor', default=None)
BUSINESS = {'Task','Department','Employee','Project','WorkCategory','WorkRequest','ChecklistItem',
            'TaskParticipation','ApprovalStep','EmployeeFeedback','PeriodReport','ReportSubmission',
            'ReportReview','ReportFile','CalendarExportPolicy','WorkSeries','WorkOccurrence','Notice',
            'ScheduleExpectation','ScheduleOutcome','DeadlinePolicy','RequestEvent','RequestAllocation'}
PRIVATE = {'PersonalBoard','PersonalColumn','PersonalCard','PersonalTag','PersonalSchedule'}
ACCOUNT_FIELDS = {'user_id','employee_id','action','reason','actor_id','actor_name','created_at'}


def write(action, target_type, target_id, label='', before=None, after=None, actor=None):
    actor = actor or _actor.get()
    authenticated = actor and actor.is_authenticated
    employee = getattr(actor,'employee',None) if authenticated else None
    return SystemAudit.objects.create(action=action, target_type=target_type, target_id=str(target_id or ''),
        target_label=label[:200], before=before or {}, after=after or {},
        actor_id_snapshot=actor.pk if authenticated else None,
        actor_name=(employee.name if employee else actor.get_username())[:150] if authenticated else '시스템')


class AuditContext:
    def __init__(self, get_response): self.get_response=get_response
    def __call__(self, request):
        token=_actor.set(request.user)
        try: return self.get_response(request)
        finally: _actor.reset(token)


def tracked(sender):
    if sender._meta.apps is not apps: return False  # Never audit historical migration models.
    return ((sender._meta.app_label=='tasks' and sender.__name__ in BUSINESS | PRIVATE) or
            (sender._meta.app_label=='accounts' and sender.__name__=='AccountEvent'))


def values(obj):
    name=type(obj).__name__
    if name in PRIVATE:
        return {'private':True,'id':obj.pk}  # No memo titles, bodies, tags, paths or calendar tokens.
    if (name=='Task' and obj.status=='draft') or (name=='PeriodReport' and obj.state=='draft'):
        return {'private':True,'id':obj.pk,'status':'draft'}
    result={}
    for field in obj._meta.concrete_fields:
        if name=='AccountEvent' and field.attname not in ACCOUNT_FIELDS: continue
        value=getattr(obj,field.attname)
        if field.get_internal_type() in ['DateTimeField','DateField','TimeField']:
            value=value.isoformat() if value else None
        elif field.get_internal_type() == 'FileField': value=bool(value)
        elif field.get_internal_type() == 'UUIDField':value=str(value) if value else None
        result[str(field.verbose_name) if not field.is_relation else field.attname]=value
    return result


def label(obj):
    if type(obj).__name__ in PRIVATE or (type(obj).__name__=='Task' and obj.status=='draft') or (type(obj).__name__=='PeriodReport' and obj.state=='draft'): return '비공개 개인 항목'
    return str(getattr(obj,'title',None) or getattr(obj,'name',None) or f'{obj._meta.verbose_name} #{obj.pk}')[:200]


def before_save(sender,instance,raw=False,**kwargs):
    if raw or not tracked(sender): return
    old=sender.objects.filter(pk=instance.pk).first() if instance.pk else None
    instance._audit_before=values(old) if old else {}


def after_save(sender,instance,created,raw=False,**kwargs):
    if raw or not tracked(sender): return
    before=getattr(instance,'_audit_before',{}); after=values(instance)
    if before!=after or created:
        write('create' if created else 'update', sender.__name__, instance.pk,label(instance),before,after)
    elif sender.__name__ in PRIVATE:
        write('update',sender.__name__,instance.pk,'비공개 개인 항목',after={'private':True})


def deleted(sender,instance,**kwargs):
    if tracked(sender): write('delete',sender.__name__,instance.pk,label(instance),before=values(instance))


def login_event(sender,request,user,**kwargs):
    from accounts.idle import mark_activity
    mark_activity(request)
    write('login','Account',user.pk,'로그인',actor=user)


def logout_event(sender,request,user,**kwargs):
    if user and user.is_authenticated: write('logout','Account',user.pk,'로그아웃',actor=user)


def watch_change(sender, instance, action, reverse, pk_set, **kwargs):
    if action in ['post_add','post_remove','post_clear']:
        write('update','TaskWatchers',instance.pk,'참조자 변경',after={'operation':action,'reverse':reverse,'ids':sorted(pk_set or [])})


def register():
    pre_save.connect(before_save,dispatch_uid='workhub_audit_before',weak=False)
    post_save.connect(after_save,dispatch_uid='workhub_audit_after',weak=False)
    pre_delete.connect(deleted,dispatch_uid='workhub_audit_delete',weak=False)
    user_logged_in.connect(login_event,dispatch_uid='workhub_audit_login',weak=False)
    user_logged_out.connect(logout_event,dispatch_uid='workhub_audit_logout',weak=False)
    from .models import Task
    m2m_changed.connect(watch_change,sender=Task.watchers.through,dispatch_uid='workhub_audit_watchers',weak=False)
