from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.http import Http404
from django.shortcuts import get_object_or_404, render, redirect
from django.utils import timezone
from django.views.decorators.http import require_POST
from .models import Task, Project, WorkRequest, ChecklistItem
from .permissions import (is_manager, is_company_admin, profile, visible_projects, can_manage_project,
                          visible_tasks, visible_requests, can_edit, can_report)
from .forms import ProjectForm, WorkRequestForm, TaskForm, ChecklistForm, CloseRequestForm
from .workflow import record, transition
from .alerts import action_alerts


def ensure_profile(user):
    if not is_company_admin(user) and not profile(user): raise PermissionDenied('소속 설정이 필요합니다.')


def project_summary(project, tasks):
    tasks = [t for t in tasks if t.project_id == project.pk]
    total = len(tasks)
    return {'project':project,'total':total,'done':sum(t.status=='done' for t in tasks),
            'progress':round(sum(t.progress for t in tasks)/total) if total else 0,
            'late':sum(t.deadline=='overdue' for t in tasks),'stale':sum(t.needs_report for t in tasks),
            'blocked':sum(t.status=='blocked' for t in tasks),'review':sum(t.status=='review' for t in tasks),
            'tasks':tasks,'empty':not total,
            'late_project':project.due_date < timezone.localdate() and (not total or any(t.status!='done' for t in tasks))}


@login_required
def projects(request):
    ensure_profile(request.user)
    tasks = list(visible_tasks(request.user))
    rows = [project_summary(p,tasks) for p in visible_projects(request.user)]
    return render(request,'tasks/projects.html',{'rows':rows,'can_create':is_manager(request.user),'standalone_count':sum(not t.project_id and t.status not in ['done','review'] for t in tasks)})


@login_required
def project_detail(request,pk):
    project = get_object_or_404(visible_projects(request.user),pk=pk)
    summary = project_summary(project,list(visible_tasks(request.user).filter(project=project)))
    return render(request,'tasks/project_detail.html',{'project':project,'summary':summary,'can_manage':can_manage_project(request.user,project),'requests':visible_requests(request.user).filter(project=project,task__isnull=True,closed_at__isnull=True)})


@login_required
def project_edit(request,pk=None):
    ensure_profile(request.user)
    if not is_manager(request.user): raise PermissionDenied
    project = get_object_or_404(visible_projects(request.user),pk=pk) if pk else None
    if project and not can_manage_project(request.user,project): raise PermissionDenied
    form = ProjectForm(request.POST if request.method=='POST' else None,instance=project,user=request.user)
    if request.method=='POST' and form.is_valid():
        project = form.save()
        return redirect('project_detail',pk=project.pk)
    return render(request,'tasks/operation_form.html',{'title':'프로젝트 수정' if pk else '프로젝트 등록','form':form})


@login_required
def intake_list(request):
    ensure_profile(request.user)
    requests = visible_requests(request.user)
    state = request.GET.get('state','pending')
    if state=='assigned': requests=requests.filter(task__isnull=False)
    elif state=='closed': requests=requests.filter(closed_at__isnull=False)
    else:
        state='pending'
        requests=requests.filter(task__isnull=True,closed_at__isnull=True)
    return render(request,'tasks/intake_list.html',{'requests':requests,'state':state,'managing':is_manager(request.user)})


@login_required
def intake_new(request):
    ensure_profile(request.user)
    form = WorkRequestForm(request.POST if request.method=='POST' else None,user=request.user)
    if request.method=='POST' and form.is_valid():
        intake=form.save(commit=False)
        intake.creator=request.user
        intake.creator_name=request.user.get_username()
        intake.save()
        return redirect('intake_detail',pk=intake.pk)
    return render(request,'tasks/operation_form.html',{'title':'업무 요청 접수','form':form,'hint':'담당자가 정해지기 전이라도 요청을 기록하세요. 관리자가 담당자와 기한을 확인해 업무로 배정합니다.'})


@login_required
def intake_detail(request,pk):
    intake=get_object_or_404(visible_requests(request.user),pk=pk)
    return render(request,'tasks/intake_detail.html',{'intake':intake,'can_assign':is_manager(request.user),'can_close':is_manager(request.user) or intake.creator_id==request.user.pk})


@login_required
@transaction.atomic
def intake_assign(request,pk):
    if not is_manager(request.user): raise PermissionDenied
    intake=get_object_or_404(visible_requests(request.user).select_for_update(),pk=pk)
    if intake.task_id: return redirect('task_detail',pk=intake.task_id)
    if intake.closed_at: raise PermissionDenied('취소된 요청입니다.')
    initial={'title':intake.title,'description':intake.description,'completion_criteria':intake.description,'project':intake.project,'start_date':timezone.localdate(),'due_date':intake.requested_due_date,'status':'todo'}
    form=TaskForm(request.POST if request.method=='POST' else None,initial=initial,user=request.user)
    form.fields['assignee'].queryset=form.fields['assignee'].queryset.filter(department=intake.department)
    form.fields['project'].queryset=visible_projects(request.user).filter(department=intake.department)
    if request.method=='POST' and form.is_valid():
        task=form.save()
        intake.task=task
        intake.assigned_at=timezone.now()
        intake.save(update_fields=['task','assigned_at'])
        record(task,request.user,'created',f'요청 #{intake.pk}에서 업무 배정',{'request_id':intake.pk})
        return redirect('task_detail',pk=task.pk)
    return render(request,'tasks/operation_form.html',{'title':'요청을 업무로 배정','form':form,'hint':f'요청 #{intake.pk} · {intake.department} 담당자를 지정하세요.'})


@login_required
@transaction.atomic
def intake_close(request,pk):
    intake=get_object_or_404(visible_requests(request.user).select_for_update(),pk=pk)
    if not (is_manager(request.user) or intake.creator_id==request.user.pk): raise PermissionDenied
    if intake.task_id or intake.closed_at: raise PermissionDenied('배정 또는 취소된 요청은 다시 취소할 수 없습니다.')
    form=CloseRequestForm(request.POST if request.method=='POST' else None)
    if request.method=='POST' and form.is_valid():
        intake.closed_at=timezone.now()
        intake.close_reason=request.user.get_username()+': '+form.cleaned_data['reason']
        intake.save(update_fields=['closed_at','close_reason'])
        return redirect('intake_detail',pk=pk)
    return render(request,'tasks/operation_form.html',{'title':'요청 취소','form':form,'hint':'기록을 삭제하지 않고 취소 사유를 보존합니다.'})


@login_required
@require_POST
def accept(request,pk):
    get_object_or_404(visible_tasks(request.user),pk=pk)
    try: transition(request.user,pk,'accept',{})
    except ValidationError: raise PermissionDenied('배정을 확인할 수 없는 상태입니다.')
    return redirect('task_detail',pk=pk)


@login_required
@transaction.atomic
def checklist_add(request,pk):
    task=get_object_or_404(visible_tasks(request.user).select_for_update(),pk=pk)
    if not can_edit(request.user,task) or task.status in ['review','done']: raise PermissionDenied
    form=ChecklistForm(request.POST if request.method=='POST' else None)
    if request.method=='POST' and form.is_valid():
        item=form.save(commit=False)
        item.task=task
        item.save()
        record(task,request.user,'checklist','확인 항목 추가: '+item.title,{'item_id':item.pk,'required':item.required})
        return redirect('task_detail',pk=pk)
    return render(request,'tasks/operation_form.html',{'title':'체크리스트 항목 추가','form':form})


@login_required
@require_POST
@transaction.atomic
def checklist_update(request,pk,item_pk):
    task=get_object_or_404(visible_tasks(request.user).select_for_update(),pk=pk)
    item=get_object_or_404(ChecklistItem,task=task,pk=item_pk,retired=False)
    if task.status in ['review','done']: raise PermissionDenied
    action=request.POST.get('action')
    if action=='retire':
        if not is_manager(request.user) or not can_edit(request.user,task): raise PermissionDenied
        item.retired=True
        message='확인 항목 제외: '
    elif action in ['check','uncheck']:
        if not can_report(request.user,task): raise PermissionDenied
        item.checked=action=='check'
        item.checked_at=timezone.now() if item.checked else None
        message='확인 완료: ' if item.checked else '확인 해제: '
    else: raise Http404
    item.save()
    record(task,request.user,'checklist',message+item.title,{'item_id':item.pk,'checked':item.checked,'retired':item.retired})
    return redirect('task_detail',pk=pk)


@login_required
def alerts(request):
    ensure_profile(request.user)
    return render(request,'tasks/alerts.html',{'alerts':action_alerts(request.user)})


@login_required
def guide(request):
    return render(request,'tasks/guide_v05.html')
