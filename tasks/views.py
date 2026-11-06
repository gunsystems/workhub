import calendar
from datetime import date, timedelta
from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.decorators import login_required
from django.utils import timezone
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Q
from django.http import Http404
from .permissions import is_manager, can_review, can_report, can_manage_employee
from .workflow import record, snapshot, transition
from .metrics import employee_metrics
from .permissions import profile, is_company_admin, visible_tasks, visible_departments, visible_employees, can_edit
from .models import Task, Department, Employee, TaskEvent, EmployeeFeedback
from .forms import TaskForm, ProgressForm, SubmitForm, ReviewForm, FeedbackForm

@login_required
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
    if department.isdigit(): tasks = tasks.filter(assignee__department_id=department)
    if employee.isdigit(): tasks = tasks.filter(assignee_id=employee)
    status = request.GET.get('status','')
    if status in dict(Task.STATUS): tasks=tasks.filter(status=status)
    tasks=list(tasks)
    days=[]
    for offset in range((end-start).days+1):
        day=start+timedelta(days=offset)
        days.append({'date':day,'today':day==today,'outside':day.month!=selected.month,'tasks':[t for t in tasks if t.start_date<=day<=t.due_date]})
    query=request.GET.copy()
    for key in ['date','view']: query.pop(key,None)
    return render(request,'tasks/calendar.html',{'days':days,'mode':mode,'selected':selected,'today':today,'prev':prev,'next':nxt,'query':query.urlencode(),'departments':visible_departments(request.user),'employees':visible_employees(request.user).select_related('department'),'department':department,'employee':employee,'status':status,'statuses':Task.STATUS,'count':len(tasks),'overdue':[t for t in tasks if t.deadline=='overdue'],'soon':[t for t in tasks if t.deadline=='soon']})

@login_required
def detail(request,pk):
    task = get_object_or_404(visible_tasks(request.user), pk=pk)
    sensitive = is_company_admin(request.user) or can_review(request.user,task) or task.assignee.user_id == request.user.pk
    events = task.events.all()
    if not sensitive:
        events = events.exclude(kind__in=['approved','feedback','returned'])
    return render(request,'tasks/detail.html',{'task':task,'can_edit':can_edit(request.user, task) and task.status not in ['review','done'], 'can_review':can_review(request.user, task),'can_report':can_report(request.user,task),'sensitive':sensitive,'events':events})

@login_required
@transaction.atomic
def edit(request,pk=None):
    if not is_company_admin(request.user) and not profile(request.user):
        return render(request, 'tasks/access_pending.html', status=403)
    task=get_object_or_404(visible_tasks(request.user).select_for_update(),pk=pk) if pk else None
    if task and not can_edit(request.user, task):
        raise PermissionDenied('이 업무를 수정할 권한이 없습니다.')
    if task and task.status in ['review','done']:
        raise PermissionDenied('검토 대기 또는 완료 업무는 일반 수정할 수 없습니다.')
    before = snapshot(task) if task else None
    form=TaskForm(request.POST if request.method == 'POST' else None,instance=task,user=request.user)
    if request.method=='POST' and form.is_valid():
        task=form.save()
        record(task,request.user,'changed' if pk else 'created',form.cleaned_data.get('change_reason') or ('업무 정보 수정' if pk else '업무 등록'),{'before':before})
        return redirect('task_detail',pk=task.pk)
    return render(request,'tasks/form.html',{'form':form,'task':task})


@login_required
def dashboard(request):
    employee = profile(request.user)
    if not is_company_admin(request.user) and not employee:
        return render(request,'tasks/access_pending.html',status=403)
    managing = is_manager(request.user) and request.GET.get('scope') != 'mine'
    tasks = visible_tasks(request.user).prefetch_related('events')
    employees = visible_employees(request.user).select_related('department')
    if not managing:
        tasks = tasks.filter(assignee=employee) if employee else tasks.none()
        employees = employees.filter(pk=employee.pk) if employee else employees.none()
    department_id = request.GET.get('department','')
    employee_id = request.GET.get('employee','')
    if department_id.isdigit():
        tasks = tasks.filter(assignee__department_id=department_id)
        employees = employees.filter(department_id=department_id)
    if employee_id.isdigit():
        tasks = tasks.filter(assignee_id=employee_id)
        employees = employees.filter(pk=employee_id)
    today = timezone.localdate()
    try:
        month = date.fromisoformat(request.GET.get('month','')+'-01')
    except ValueError:
        month = today.replace(day=1)
    end = month.replace(day=calendar.monthrange(month.year,month.month)[1])
    tasks = list(tasks)
    if managing and not is_company_admin(request.user):
        employees = employees.filter(Q(role='employee') | Q(pk=employee.pk)).exclude(user__is_superuser=True)
    rows = [employee_metrics(e,tasks,month,end) for e in employees]
    for row in rows:
        row['can_feedback'] = can_manage_employee(request.user,row['employee'])
    rows.sort(key=lambda row:(not row['attention'],row['score'] if row['score'] is not None else 101,row['employee'].name))
    bucket = request.GET.get('bucket','active')
    buckets = {
        'active': [t for t in tasks if t.status != 'done'],
        'overdue': [t for t in tasks if t.deadline == 'overdue' and t.status != 'review'],
        'stale': [t for t in tasks if t.needs_report],
        'not_started': [t for t in tasks if t.not_started],
        'review': [t for t in tasks if t.status == 'review'],
        'done': [t for t in tasks if t.status == 'done'],
    }
    attention_count = sum(r['attention'] for r in rows)
    attention_only = request.GET.get('attention') == '1'
    if attention_only: rows = [row for row in rows if row['attention']]
    if bucket not in buckets: bucket='active'
    query = request.GET.copy()
    query.pop('bucket',None)
    return render(request,'tasks/dashboard.html',{'managing':managing,'is_manager':is_manager(request.user),'rows':rows,'tasks':buckets[bucket],'counts':{k:len(v) for k,v in buckets.items()},'bucket':bucket,'query':query.urlencode(),'month':month,'departments':visible_departments(request.user),'employees':visible_employees(request.user),'department':department_id,'employee':employee_id,'scope':request.GET.get('scope',''),'attention_count':attention_count,'attention_only':attention_only,'today':today,'employee_feedbacks':EmployeeFeedback.objects.filter(employee__in=employees).select_related('employee')[:10],'feedbacks':TaskEvent.objects.filter(task_id__in=[t.pk for t in tasks if is_company_admin(request.user) or t.assignee.user_id == request.user.pk or (managing and t.assignee.role == 'employee' and not (t.assignee.user_id and t.assignee.user.is_superuser))],kind__in=['feedback','returned','approved']).select_related('task')[:10]})


@login_required
def task_action(request,pk,action):
    forms = {'progress':ProgressForm,'submit':SubmitForm,'review':ReviewForm,'feedback':FeedbackForm}
    titles = {'progress':'진행 보고','submit':'완료 보고','review':'완료 검토','feedback':'관리자 피드백'}
    if action not in forms: raise Http404
    task = get_object_or_404(visible_tasks(request.user),pk=pk)
    allowed = can_review(request.user,task) if action in ['review','feedback'] else can_report(request.user,task)
    if not allowed: raise PermissionDenied
    form = forms[action](request.POST if request.method == 'POST' else None)
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
