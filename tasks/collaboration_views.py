from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_POST
from django.shortcuts import get_object_or_404,render,redirect
from django.utils import timezone
from .models import Task, WorkNotification
from .permissions import visible_tasks,can_report,can_review,can_edit,is_company_admin


def official_detail(request,task):
    steps=list(task.approval_steps.all())
    current=next((s for s in steps if s.decision!='approved'),None)
    return render(request,'tasks/official_detail.html',{
        'task':task,'steps':steps,'current':current,'assignments':task.assignments.select_related('employee__department'),
        'can_edit':can_edit(request.user,task) and task.status not in ['review','done','cancelled'],
        'can_report':can_report(request.user,task),'can_review':can_review(request.user,task),
        'primary':task.assignee.user_id==request.user.pk,'owner':task.creator_id==request.user.pk,
        'executive':is_company_admin(request.user),'events':task.events.all(),
        'group_tasks':visible_tasks(request.user).filter(group_key=task.group_key) if task.group_key else [],
        'checklist':task.checklist.filter(retired=False),
    })


@login_required
def official_list(request):
    status=request.GET.get('status','')
    tasks=visible_tasks(request.user)
    if status in dict(Task.STATUS): tasks=tasks.filter(status=status)
    else: tasks=tasks.exclude(status__in=['done','cancelled'])
    query=request.GET.get('q','').strip()[:200]
    if query: tasks=tasks.filter(title__icontains=query)
    return render(request,'tasks/official_list.html',{'tasks':tasks[:200],'statuses':Task.STATUS,'status':status,'query':query})


@login_required
def notifications(request):
    items=WorkNotification.objects.filter(user=request.user,task__in=visible_tasks(request.user)).select_related('task')
    return render(request,'tasks/notifications.html',{'notifications':items[:100]})


@login_required
@require_POST
def read_notification(request,pk):
    item=get_object_or_404(WorkNotification,pk=pk,user=request.user,task__in=visible_tasks(request.user))
    item.read_at=timezone.now(); item.save(update_fields=['read_at'])
    return redirect('task_detail',pk=item.task_id)
