import calendar
from datetime import date, timedelta
from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.decorators import login_required
from django.utils import timezone
from .models import Task, Department, Employee
from .forms import TaskForm

@login_required
def calendar_view(request):
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
    tasks = Task.objects.select_related('assignee__department')
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
    return render(request,'tasks/calendar.html',{'days':days,'mode':mode,'selected':selected,'today':today,'prev':prev,'next':nxt,'query':query.urlencode(),'departments':Department.objects.all(),'employees':Employee.objects.select_related('department'),'department':department,'employee':employee,'status':status,'statuses':Task.STATUS,'count':len(tasks),'overdue':[t for t in tasks if t.deadline=='overdue'],'soon':[t for t in tasks if t.deadline=='soon']})

@login_required
def detail(request,pk):
    return render(request,'tasks/detail.html',{'task':get_object_or_404(Task.objects.select_related('assignee__department'),pk=pk)})

@login_required
def edit(request,pk=None):
    task=get_object_or_404(Task,pk=pk) if pk else None
    form=TaskForm(request.POST or None,instance=task)
    if request.method=='POST' and form.is_valid():
        task=form.save()
        return redirect('task_detail',pk=task.pk)
    return render(request,'tasks/form.html',{'form':form,'task':task})
