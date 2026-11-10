from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_POST
from django.shortcuts import get_object_or_404,render,redirect
from django.utils import timezone
from .models import Task, WorkNotification,PersonalReminder
from .permissions import visible_departments,visible_employees,performing_department,performing_employee,visible_tasks,can_report,can_review,can_edit,is_company_admin,can_override_work


def official_detail(request,task):
    from .permissions import visible_requests
    from .google_calendar import official_allowed
    from .calendar_views import task_calendar_registered
    steps=list(task.approval_steps.all())
    current=next((s for s in steps if s.decision!='approved'),None)
    conflicts=[]
    if task.activity_start and task.activity_end:
        from django.db.models import Q
        performers=list(task.participants.values_list('pk',flat=True))+[task.assignee_id]
        conflicts=visible_tasks(request.user).filter(Q(assignee_id__in=performers)|Q(participants__pk__in=performers),
            activity_start__lt=task.activity_end,activity_end__gt=task.activity_start).exclude(pk=task.pk).exclude(status__in=['done','cancelled','draft']).distinct()[:10]
    return render(request,'tasks/official_detail.html',{
        'source_request':visible_requests(request.user).filter(task=task).first(),'task':task,'steps':steps,'current':current,'assignments':task.assignments.select_related('employee__department'),
        'conflicts':conflicts,
        'can_edit':can_edit(request.user,task) and task.status not in ['review','done','cancelled'],
        'can_report':can_report(request.user,task),'can_review':can_review(request.user,task),
        'primary':task.assignee.user_id==request.user.pk,'owner':task.creator_id==request.user.pk,
        'executive':can_override_work(request.user),'events':task.events.all(),
        'group_tasks':visible_tasks(request.user).filter(group_key=task.group_key) if task.group_key else [],
        'checklist':task.checklist.filter(retired=False),
        'can_export_calendar':official_allowed(request.user,task),'calendar_registered':task_calendar_registered(request.user,task),
    })


@login_required
def official_list(request):
    status=request.GET.get('status','')
    tasks=visible_tasks(request.user)
    if status == 'all': pass
    elif status in dict(Task.STATUS): tasks=tasks.filter(status=status)
    else:
        status=''
        tasks=tasks.exclude(status__in=['done','cancelled'])
    department=request.GET.get('department','')
    employee=request.GET.get('employee','')
    if department.isdigit(): tasks=tasks.filter(performing_department(department)).distinct()
    if employee.isdigit(): tasks=tasks.filter(performing_employee(employee)).distinct()
    query=request.GET.get('q','').strip()[:200]
    if query: tasks=tasks.filter(title__icontains=query)
    from .models import WorkCategory
    category=request.GET.get('category','');mode=request.GET.get('mode','')
    if category.isdigit():tasks=tasks.filter(category_id=category)
    if mode in ['report','event']:tasks=tasks.filter(management_mode=mode)
    return render(request,'tasks/official_list.html',{'tasks':tasks[:200],'categories':WorkCategory.objects.filter(is_active=True),'category':category,'mode':mode,'departments':visible_departments(request.user),'employees':visible_employees(request.user),'department':department,'employee':employee,'statuses':Task.STATUS,'status':status,'query':query})


@login_required
def notifications(request):
    items=WorkNotification.objects.filter(user=request.user,task__in=visible_tasks(request.user)).select_related('task')
    personal=PersonalReminder.objects.filter(card__column__board__owner=request.user,delivered_at__isnull=False).select_related('card')
    from .models import RequestNotification
    from .permissions import visible_requests
    notices=RequestNotification.objects.filter(user=request.user,request__in=visible_requests(request.user)).select_related('request','event').order_by('-event__created_at')
    return render(request,'tasks/notifications.html',{'notifications':items[:100],'personal_notifications':personal[:100],'request_notifications':notices[:100]})


@login_required
@require_POST
def read_notification(request,pk):
    item=get_object_or_404(WorkNotification,pk=pk,user=request.user,task__in=visible_tasks(request.user))
    item.read_at=timezone.now(); item.save(update_fields=['read_at'])
    return redirect('task_detail',pk=item.task_id)
