from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_POST
from django.shortcuts import get_object_or_404,render,redirect
from django.utils import timezone
from .models import Task, WorkNotification,PersonalReminder
from .permissions import visible_departments,visible_employees,performing_department,performing_employee,visible_tasks,can_report,can_review,can_edit,is_company_admin,can_override_work


def official_detail(request,task):
    from .transfers import can_request, visible as visible_transfers
    from .progress_reporting import next_report_number
    from .permissions import visible_requests
    from .google_calendar import official_allowed
    from .calendar_views import task_calendar_registered
    steps=list(task.approval_steps.select_related('approver__employee__department'))
    current=next((s for s in steps if s.decision!='approved'),None)
    my_assignment=task.assignments.filter(employee__user=request.user,employee__is_active=True).first()
    assignment_count=task.assignments.count()
    pending_performers=task.assignments.filter(completed_at__isnull=True).exclude(employee__user=request.user).exists()
    conflicts=[]
    if task.activity_start and task.activity_end:
        from django.db.models import Q
        performers=list(task.participants.values_list('pk',flat=True))+[task.assignee_id]
        conflicts=visible_tasks(request.user).filter(Q(assignee_id__in=performers)|Q(participants__pk__in=performers),
            activity_start__lt=task.activity_end,activity_end__gt=task.activity_start).exclude(pk=task.pk).exclude(status__in=['done','cancelled','draft']).distinct()[:10]
    return render(request,'tasks/official_detail.html',{
        'source_request':visible_requests(request.user).filter(task=task).first(),'task':task,'steps':steps,'current':current,'assignments':task.assignments.select_related('employee__department'),
        'conflicts':conflicts,'can_transfer':can_request(request.user,task),'transfers':visible_transfers(request.user).filter(task=task),
        'next_progress_number':next_report_number(task,request.user),'my_assignment':my_assignment,'assignment_count':assignment_count,'pending_performers':pending_performers,
        'can_edit':can_edit(request.user,task) and task.status not in ['review','done','cancelled'],
        'can_report':can_report(request.user,task),'can_review':can_review(request.user,task),
        'primary':task.assignee.user_id==request.user.pk,'owner':task.creator_id==request.user.pk,
        'executive':can_override_work(request.user),'events':task.events.select_related('author__employee__department'),
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
    from .models import QuickDelivery
    from .quick import visible as visible_quick
    quick_items=QuickDelivery.objects.filter(user=request.user,channel='app',event__request__in=visible_quick(request.user)).select_related('event__request').order_by('-event__created_at')
    from .models import OffboardingNotice, OffboardingEvent
    from django.db.models import Exists, OuterRef
    from .offboarding import visible_items
    followups=OffboardingNotice.objects.filter(user=request.user,item__in=visible_items(request.user)).select_related('item__case__employee').annotate(cancel_ack=Exists(OffboardingEvent.objects.filter(item_id=OuterRef('item_id'),actor_id=OuterRef('item__assignee_id'),action='퇴사 취소 확인')))[:100]
    for row in followups:
        cancelled=row.item.case.state=='cancelled'
        row.closed=(cancelled and row.cancel_ack) or row.item.case.state=='done' or (not cancelled and row.item.state=='approved')
        row.current_state=('취소 확인 완료' if row.cancel_ack else '퇴사 처리 취소 · 확인 필요') if cancelled else ('확인 완료' if row.closed else row.item.get_state_display())
    from .leave_notifications import enabled,actionable
    from .leave import visible as visible_leave
    from .models import LeaveNotice
    leave_items=LeaveNotice.objects.filter(user=request.user,event__request__in=visible_leave(request.user)).select_related('event__request')[:100] if enabled() else []
    for row in leave_items:row.actionable=actionable(row)
    from .models import ProposalNotice
    from .proposals import visible as visible_proposals
    proposal_items=ProposalNotice.objects.filter(user=request.user,event__proposal__in=visible_proposals(request.user)).select_related('event__proposal')[:100]
    return render(request,'tasks/notifications.html',{'proposal_notifications':proposal_items,'leave_notifications':leave_items,'followup_notifications':followups,'quick_notifications':quick_items[:100],'notifications':items[:100],'personal_notifications':personal[:100],'request_notifications':notices[:100]})


@login_required
@require_POST
def read_notification(request,pk):
    item=get_object_or_404(WorkNotification,pk=pk,user=request.user,task__in=visible_tasks(request.user))
    item.read_at=timezone.now(); item.save(update_fields=['read_at'])
    return redirect('task_detail',pk=item.task_id)
