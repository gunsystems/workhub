"""Administrator-only recoverable task removal, retaining reports and documents."""
from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import F
from django.shortcuts import get_object_or_404,render,redirect
from django.utils import timezone
from django.views.decorators.http import require_http_methods
from django.http import Http404
from .models import Task,TaskTrash,WorkSeries,CalendarExport
from datetime import timedelta
from .audit import write


def allowed(user):
    from accounts.lifecycle import system_admin
    return system_admin(user)


class DeleteForm(forms.Form):
    revision=forms.IntegerField(widget=forms.HiddenInput)
    reason=forms.CharField(label='삭제 사유',max_length=1000,widget=forms.Textarea(attrs={'rows':3}))


@login_required
@require_http_methods(['GET','POST'])
@transaction.atomic
def remove(request,pk):
    if not allowed(request.user):raise PermissionDenied
    task=get_object_or_404(Task.all_objects.select_for_update().select_related('assignee__department'),pk=pk,is_deleted=False)
    form=DeleteForm(request.POST if request.method=='POST' else None,initial={'revision':task.revision})
    if request.method=='POST' and form.is_valid():
        from .models import Meeting
        meeting=Meeting.objects.filter(task=task).first()
        if meeting and meeting.provider=='teams' and (task.status!='cancelled' or meeting.sync_state!='synced'):
            form.add_error(None,'Teams 미팅은 먼저 미팅 취소 후 연결 완료를 확인해 주세요. 외부 회의 초대까지 취소한 뒤 삭제할 수 있습니다.')
        elif form.cleaned_data['revision']!=task.revision:
            form.add_error(None,'업무가 변경되었습니다. 다시 열어 내용을 확인해 주세요.')
        else:
            TaskTrash.objects.create(task=task,previous_status=task.status,reason=form.cleaned_data['reason'],deleted_by=request.user)
            Task.all_objects.filter(pk=task.pk).update(is_deleted=True,status='cancelled',revision=F('revision')+1)
            WorkSeries.objects.filter(template_task=task,enabled=True).update(enabled=False)
            CalendarExport.objects.filter(task=task).update(updated_at=timezone.now()-timedelta(days=3650))
            write('delete','Task',task.pk,task.title,before={'status':task.status,'is_deleted':False},after={'is_deleted':True,'reason':form.cleaned_data['reason'],'recoverable':True},actor=request.user)
            messages.success(request,'휴지통으로 이동했습니다. 외부 캘린더에는 다음 동기화 때 반영됩니다.')
            return redirect('task_trash')
    return render(request,'tasks/task_delete.html',{'task':task,'form':form})


@login_required
@require_http_methods(['GET','POST'])
@transaction.atomic
def listing(request):
    if not allowed(request.user):raise PermissionDenied
    if request.method=='POST':
        if not request.POST.get('task_id','').isdigit():raise Http404
        task=get_object_or_404(Task.all_objects.select_for_update(),pk=request.POST.get('task_id'),is_deleted=True)
        record=get_object_or_404(TaskTrash,task=task,restored_at__isnull=True)
        if request.POST.get('revision')!=str(task.revision):
            messages.error(request,'다른 변경이 반영되었습니다. 목록을 새로 확인해 주세요.')
        else:
            Task.all_objects.filter(pk=task.pk).update(is_deleted=False,status=record.previous_status,revision=F('revision')+1)
            record.restored_at=timezone.now();record.restored_by=request.user;record.save(update_fields=['restored_at','restored_by'])
            CalendarExport.objects.filter(task=task).update(updated_at=timezone.now()-timedelta(days=3650))
            write('restore','Task',task.pk,task.title,before={'is_deleted':True},after={'is_deleted':False,'status':record.previous_status},actor=request.user)
            messages.success(request,'복구했습니다. 정기 업무의 자동 생성은 필요하면 다시 설정해 주세요.')
        return redirect('task_trash')
    records=TaskTrash.objects.filter(restored_at__isnull=True,task__is_deleted=True).select_related('task__assignee__department','deleted_by').order_by('-deleted_at')[:200]
    return render(request,'tasks/task_trash.html',{'records':records})
