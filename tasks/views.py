import calendar
from datetime import date, timedelta
from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.decorators import login_required
from django.utils import timezone
from django.core.exceptions import PermissionDenied
from .permissions import profile, is_company_admin, visible_tasks, visible_departments, visible_employees, can_edit
from .models import Task, Department, Employee
from .forms import TaskForm

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
    return render(request,'tasks/detail.html',{'task':task,'can_edit':can_edit(request.user, task)})

@login_required
def edit(request,pk=None):
    if not is_company_admin(request.user) and not profile(request.user):
        return render(request, 'tasks/access_pending.html', status=403)
    task=get_object_or_404(visible_tasks(request.user),pk=pk) if pk else None
    if task and not can_edit(request.user, task):
        raise PermissionDenied('이 업무를 수정할 권한이 없습니다.')
    form=TaskForm(request.POST if request.method == 'POST' else None,instance=task,user=request.user)
    if request.method=='POST' and form.is_valid():
        task=form.save()
        return redirect('task_detail',pk=task.pk)
    return render(request,'tasks/form.html',{'form':form,'task':task})
