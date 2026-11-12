import calendar
from datetime import date, timedelta
from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.decorators import login_required
from django.views.decorators.cache import never_cache
from django.utils import timezone
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Q
from django.http import Http404
from .permissions import is_manager, can_review, can_report, can_manage_employee
from .workflow import record, snapshot, transition
from .metrics import employee_metrics
from .permissions import profile, is_company_admin, visible_tasks, visible_departments, visible_employees, can_edit
from .permissions import can_view_company, performing_department, performing_employee
from .models import Task, Department, Employee, TaskEvent, EmployeeFeedback
from .forms import TaskForm, ProgressForm, SubmitForm, ReviewForm, FeedbackForm

@login_required
@never_cache
def calendar_view(request):
    if not is_company_admin(request.user) and not profile(request.user):
        return render(request, 'tasks/access_pending.html', status=403)
    today = timezone.localdate()
    try: selected = date.fromisoformat(request.GET.get('date',''))
    except ValueError: selected = today
    mode = request.GET.get('view','month')
    if mode not in ['month','week','day']: mode = 'month'
    if mode == 'month':
        first = selected.replace(day=1)
        start = first - timedelta(days=(first.weekday()+1)%7)
        last = selected.replace(day=calendar.monthrange(selected.year,selected.month)[1])
        end = last + timedelta(days=(5-last.weekday())%7)
        prev = (first-timedelta(days=1)).replace(day=1)
        nxt = (last+timedelta(days=1))
    elif mode == 'week':
        start = selected-timedelta(days=(selected.weekday()+1)%7)
        end = start+timedelta(days=6)
        prev,nxt = selected-timedelta(days=7),selected+timedelta(days=7)
    else:
        start=end=selected
        prev,nxt=selected-timedelta(days=1),selected+timedelta(days=1)
    tasks = visible_tasks(request.user)
    department = request.GET.get('department','')
    employee = request.GET.get('employee','')
    if department.isdigit(): tasks = tasks.filter(performing_department(department)).distinct()
    if employee.isdigit(): tasks = tasks.filter(performing_employee(employee)).distinct()
    status = request.GET.get('status','')
    if status in dict(Task.STATUS): tasks=tasks.filter(status=status)
    tasks=list(tasks)
    from .models import PublicHoliday,LeaveHoliday
    public_holidays=dict(PublicHoliday.objects.filter(date__range=(start,end),active=True).values_list('date','name'))
    company_holidays=dict(LeaveHoliday.objects.filter(date__range=(start,end)).values_list('date','name'))
    days=[]
    for offset in range((end-start).days+1):
        day=start+timedelta(days=offset)
        items=[]
        for t in tasks:
            first=timezone.localtime(t.activity_start).date() if t.activity_start else t.start_date
            last=timezone.localtime(t.activity_end-timedelta(microseconds=1)).date() if t.activity_end else t.due_date
            if first<=day<=last:items.append(t)
        items.sort(key=lambda t:(bool(t.activity_start),t.activity_start.isoformat() if t.activity_start else '',t.pk))
        days.append({'date':day,'today':day==today,'outside':day.month!=selected.month,'tasks':items,'public_holiday':public_holidays.get(day,''),'company_holiday':company_holidays.get(day,''),'weekday':day.weekday()})
    for day in days:
        day['total'] = len(day['tasks'])
        day['more'] = max(0, day['total'] - 4) if mode == 'month' else 0
        if mode == 'month': day['tasks'] = day['tasks'][:4]
    query=request.GET.copy()
    for key in ['date','view']: query.pop(key,None)
    from .calendar_quick import CalendarQuickForm,entry_token
    person=profile(request.user)
    quick_form=CalendarQuickForm(user=request.user,auto_id='calendar_quick_%s',initial={'start_date':selected,'due_date':selected,'start_time':'10:00','end_time':'11:00','assignee':person.pk if person else None,'token':entry_token(request.user)})
    return render(request,'tasks/calendar.html',{'quick_form':quick_form,'days':days,'mode':mode,'selected':selected,'today':today,'prev':prev,'next':nxt,'query':query.urlencode(),'departments':visible_departments(request.user),'employees':visible_employees(request.user).select_related('department'),'department':department,'employee':employee,'status':status,'statuses':Task.STATUS,'count':len(tasks),'overdue':[t for t in tasks if t.deadline=='overdue'],'soon':[t for t in tasks if t.deadline=='soon']})

@login_required
def detail(request,pk):
    task = get_object_or_404(visible_tasks(request.user), pk=pk)
    if task.workflow_version == 2:
        from .collaboration_views import official_detail
        return official_detail(request,task)
    from .progress_reporting import next_report_number
    sensitive = is_company_admin(request.user) or can_review(request.user,task) or task.assignee.user_id == request.user.pk
    events = task.events.select_related('author__employee__department')
    if not sensitive:
        events = events.exclude(kind__in=['approved','feedback','returned'])
    from .google_calendar import official_allowed
    from .calendar_views import task_calendar_registered
    calendar_export_allowed = official_allowed(request.user, task)
    return render(request,'tasks/detail.html',{'task':task,'can_export_calendar':calendar_export_allowed,'calendar_registered':task_calendar_registered(request.user,task),'can_edit':can_edit(request.user, task) and task.status not in ['review','done'], 'can_review':can_review(request.user, task),'can_report':can_report(request.user,task),'next_progress_number':next_report_number(task,request.user),'sensitive':sensitive,'events':events,'checklist':task.checklist.filter(retired=False)})

@login_required
@transaction.atomic
def edit(request,pk=None):
    if not is_company_admin(request.user) and not profile(request.user):
        return render(request, 'tasks/access_pending.html', status=403)
    if pk is None and (request.method=='GET' or 'allocation' in request.POST):
        from .collaboration import edit_task
        return edit_task(request)
    task=get_object_or_404(visible_tasks(request.user).select_for_update(),pk=pk) if pk else None
    if task and task.workflow_version == 2:
        from .collaboration import edit_task
        return edit_task(request,task)
    if task and not can_edit(request.user, task):
        raise PermissionDenied('이 업무를 수정할 권한이 없습니다.')
    if task and task.status in ['review','done']:
        raise PermissionDenied('검토 대기 또는 완료 업무는 일반 수정할 수 없습니다.')
    before = snapshot(task) if task else None
    form=TaskForm(request.POST if request.method == 'POST' else None,instance=task,user=request.user,initial={'project':request.GET.get('project')} if not pk else None)
    if request.method=='POST' and form.is_valid():
        task=form.save(commit=False)
        if not pk:task.creator=request.user
        if pk:task.revision+=1
        if before and str(task.assignee_id) != before['assignee_id']:
            task.accepted_at = None
            task.assigned_at = timezone.now()
            task.last_report_at = None
        task.save()
        record(task,request.user,'changed' if pk else 'created',form.cleaned_data.get('change_reason') or ('업무 정보 수정' if pk else '업무 등록'),{'before':before})
        if not before or before['status']=='draft' or before['assignee_id']!=str(task.assignee_id):
            from .assignment_mail import enqueue
            enqueue(task,request.user,{task.assignee_id})
            from .collaboration import notify
            notify(task,'새 업무가 배정되었습니다.',external=task.assignment_messenger)
        return redirect('task_detail',pk=task.pk)
    return render(request,'tasks/form.html',{'form':form,'task':task})


@login_required
def dashboard(request):
    employee = profile(request.user)
    if not is_company_admin(request.user) and not employee:
        return render(request,'tasks/access_pending.html',status=403)
    managing = (is_manager(request.user) or can_view_company(request.user)) and request.GET.get('scope') != 'mine'
    tasks = visible_tasks(request.user).prefetch_related('events')
    employees = visible_employees(request.user).select_related('department')
    if not managing:
        tasks = tasks.filter(Q(assignee=employee)|Q(participants=employee)|Q(creator=request.user)|Q(approval_steps__approver=request.user)|Q(watchers=request.user)).distinct() if employee else tasks.none()
        employees = employees.filter(pk=employee.pk) if employee else employees.none()
    department_id = request.GET.get('department','')
    employee_id = request.GET.get('employee','')
    if department_id.isdigit():
        tasks = tasks.filter(performing_department(department_id)).distinct()
        employees = employees.filter(department_id=department_id)
    if employee_id.isdigit():
        tasks = tasks.filter(performing_employee(employee_id)).distinct()
        employees = employees.filter(pk=employee_id)
    today = timezone.localdate()
    try:
        month = date.fromisoformat(request.GET.get('month','')+'-01')
    except ValueError:
        month = today.replace(day=1)
    end = month.replace(day=calendar.monthrange(month.year,month.month)[1])
    tasks = list(tasks)
    if managing and not can_view_company(request.user):
        employees = employees.filter(Q(role='employee') | Q(pk=employee.pk)).exclude(user__is_superuser=True)
    rows = [employee_metrics(e,tasks,month,end) for e in employees]
    for row in rows:
        row['can_feedback'] = can_manage_employee(request.user,row['employee'])
    rows.sort(key=lambda row:(not row['attention'],row['score'] if row['score'] is not None else 101,row['employee'].name))
    bucket = request.GET.get('bucket','active')
    buckets = {
        'active': [t for t in tasks if t.status not in ['done','cancelled','draft']],
        'overdue': [t for t in tasks if t.deadline == 'overdue' and t.status != 'review'],
        'stale': [t for t in tasks if t.needs_report],
        'not_started': [t for t in tasks if t.not_started],
        'review': [t for t in tasks if t.status == 'review'],
        'done': [t for t in tasks if t.status == 'done'],
        'unaccepted': [t for t in tasks if t.awaiting_acceptance],
        'review_late': [t for t in tasks if t.review_overdue],
    }
    attention_count = sum(r['attention'] for r in rows)
    attention_only = request.GET.get('attention') == '1'
    if attention_only: rows = [row for row in rows if row['attention']]
    if bucket not in buckets: bucket='active'
    query = request.GET.copy()
    query.pop('bucket',None)
    from .quick import visible as visible_quick
    quick_items=visible_quick(request.user).exclude(state__in=['done','cancelled'])
    if not managing:quick_items=quick_items.filter(Q(requester=request.user)|Q(assignee__user=request.user))
    if department_id.isdigit():quick_items=quick_items.filter(assignee__department_id=department_id)
    if employee_id.isdigit():quick_items=quick_items.filter(assignee_id=employee_id)
    quick_context={'quick_items':quick_items[:10],'quick_total':quick_items.count(),'quick_pending':quick_items.filter(state='pending').count(),'quick_replied':quick_items.filter(state='replied').count()}
    return render(request,'tasks/dashboard.html',{**quick_context,'company_view':can_view_company(request.user),'managing':managing,'is_manager':is_manager(request.user),'rows':rows,'tasks':buckets[bucket],'counts':{k:len(v) for k,v in buckets.items()},'bucket':bucket,'query':query.urlencode(),'month':month,'departments':visible_departments(request.user),'employees':visible_employees(request.user),'department':department_id,'employee':employee_id,'scope':request.GET.get('scope',''),'attention_count':attention_count,'attention_only':attention_only,'today':today,'employee_feedbacks':EmployeeFeedback.objects.filter(employee__in=employees).select_related('employee')[:10],'feedbacks':TaskEvent.objects.filter(task_id__in=[t.pk for t in tasks if is_company_admin(request.user) or t.assignee.user_id == request.user.pk or (managing and t.assignee.role == 'employee' and not (t.assignee.user_id and t.assignee.user.is_superuser))],kind__in=['feedback','returned','approved']).select_related('task')[:10]})


@login_required
def task_action(request,pk,action):
    forms = {'progress':ProgressForm,'submit':SubmitForm,'review':ReviewForm,'feedback':FeedbackForm}
    titles = {'progress':'진행 보고','submit':'완료 보고','review':'완료 검토','feedback':'관리자 피드백'}
    if action not in forms: raise Http404
    task = get_object_or_404(visible_tasks(request.user),pk=pk)
    allowed = can_review(request.user,task) if action in ['review','feedback'] else can_report(request.user,task)
    if not allowed: raise PermissionDenied
    if task.workflow_version == 2 and action in ['progress','submit']:
        return redirect('official_action',pk=pk,kind=action)
    from .progress_reporting import next_report_number
    if action=='progress':titles['progress']=f'진행 보고 {next_report_number(task,request.user)}차'
    initial={'progress':min(99,task.progress),'delay_reason':task.delay_reason} if action=='progress' else {}
    form = forms[action](request.POST if request.method == 'POST' else None,initial=initial)
    if request.method == 'POST' and form.is_valid():
        try:
            transition(request.user,pk,action,form.cleaned_data)
        except Task.DoesNotExist:
            raise Http404
        except ValidationError as exc:
            form.add_error(None,exc)
        else:
            return redirect('task_detail',pk=pk)
    return render(request,'tasks/action.html',{'task':task,'form':form,'title':titles[action]})


@login_required
def employee_feedback(request,pk):
    employee = get_object_or_404(visible_employees(request.user),pk=pk)
    if not can_manage_employee(request.user,employee):
        raise PermissionDenied
    form = FeedbackForm(request.POST if request.method == 'POST' else None)
    if request.method == 'POST' and form.is_valid():
        EmployeeFeedback.objects.create(employee=employee,author=request.user,author_name=request.user.get_username(),body=form.cleaned_data['body'])
        return redirect('/dashboard/?employee='+str(employee.pk))
    return render(request,'tasks/employee_feedback.html',{'employee':employee,'form':form})
