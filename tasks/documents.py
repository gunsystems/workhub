"""Object-scoped attachments; no public Drive sharing or cross-task file search."""
from pathlib import Path
from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST
from .models import WorkDocument
from .permissions import (visible_tasks, visible_requests, visible_projects, profile,
                          is_company_admin, can_edit, can_report, can_manage_project)
from .drive_storage import configured, RepositoryError, DriveClient

EXTENSIONS = {'.pdf','.doc','.docx','.xls','.xlsx','.ppt','.pptx','.txt','.csv','.png','.jpg','.jpeg','.zip'}


def validate_file(value):
    if not value:
        return
    from django.conf import settings
    if value.size <= 0 or value.size > settings.WORKHUB_DOCUMENT_MAX_BYTES:
        raise ValidationError('파일당 20MB 이하의 내용이 있는 파일을 선택하세요.')
    if Path(value.name).suffix.lower() not in EXTENSIONS:
        raise ValidationError('PDF·Office 문서·텍스트·이미지·ZIP 파일만 등록할 수 있습니다.')


class DocumentForm(forms.Form):
    name = forms.CharField(label='자료 이름', max_length=200, required=False,
        help_text='파일을 올리는 경우 비워 두면 파일 이름을 사용합니다.')
    kind = forms.ChoiceField(label='자료 구분', choices=WorkDocument._meta.get_field('kind').choices)
    file = forms.FileField(widget=forms.ClearableFileInput(attrs={'data-upload-limit':'20971520','accept':'.pdf,.doc,.docx,.xls,.xlsx,.ppt,.pptx,.txt,.csv,.png,.jpg,.jpeg,.zip'}),label='파일 첨부 · 최대 20MB', required=False, validators=[validate_file])
    link = forms.URLField(label='외부 자료 링크', max_length=2000, required=False,
        help_text='큰 파일은 회사 공유 링크로 등록할 수 있습니다. 외부 저장소 열람 권한은 별도로 필요합니다.')

    def clean(self):
        d = super().clean()
        if bool(d.get('file')) == bool(d.get('link')):
            raise ValidationError('파일 또는 외부 링크 중 하나를 등록하세요.')
        if d.get('file') and not configured():
            self.add_error('file','자료 저장소 연결 후 파일을 등록할 수 있습니다. 관리자에게 문의하세요.')
        if d.get('link') and not d.get('name'):
            self.add_error('name','링크를 구분할 자료 이름을 입력하세요.')
        return d


def parent_for(user, kind, pk):
    scopes = {'task':visible_tasks, 'request':visible_requests, 'project':visible_projects}
    if kind not in scopes:
        raise Http404
    return get_object_or_404(scopes[kind](user), pk=pk)


def parent_kwargs(kind, obj):
    return {('work_request' if kind == 'request' else kind):obj}


def parent_url(kind, obj):
    return reverse({'task':'task_detail','request':'intake_detail','project':'project_detail'}[kind], args=[obj.pk])


def can_add(user, kind, obj):
    if is_company_admin(user):
        return True
    if not profile(user):
        return False
    if kind == 'task':
        return obj.status not in ['done','cancelled','review'] and (can_edit(user,obj) or can_report(user,obj))
    if kind == 'project':
        return can_manage_project(user,obj) or obj.owner.user_id == user.pk or visible_tasks(user).filter(
            project=obj).filter(participants__user=user).exists()
    from .requests import can_assign
    return not obj.closed_at and obj.stage != 'rejected' and (
        obj.creator_id == user.pk or obj.coordinator and obj.coordinator.user_id == user.pk or can_assign(user,obj))


@transaction.atomic
def add_document(user, kind, parent, data):
    from .audit import write
    emp = profile(user)
    f = data.get('file')
    obj = WorkDocument(**parent_kwargs(kind,parent), uploader=user,
        uploader_name=(emp.name if emp else user.get_username())[:150],
        name=data.get('name') or Path(f.name).name[:200], kind=data.get('kind','reference'),
        link=data.get('link',''), size=f.size if f else 0)
    if f:
        obj.file = f
    obj.save()
    write('create','WorkDocument',obj.pk,obj.name,
        after={'자료 구분':obj.get_kind_display(),'파일 크기':obj.size,'연결 종류':kind,'연결 번호':parent.pk},actor=user)
    return obj


@login_required
def document_add(request, kind, pk):
    obj = parent_for(request.user, kind, pk)
    if not can_add(request.user, kind, obj):
        raise PermissionDenied
    form = DocumentForm(request.POST if request.method == 'POST' else None, request.FILES or None)
    if request.method == 'POST' and form.is_valid():
        if WorkDocument.objects.filter(**parent_kwargs(kind,obj),archived_at__isnull=True).count() >= 100:
            form.add_error(None,'업무·요청·프로젝트별 자료는 최대 100개입니다.')
        else:
            try:
                add_document(request.user,kind,obj,form.cleaned_data)
            except RepositoryError as exc:
                form.add_error(None,str(exc))
            else:
                messages.success(request,'파일 업로드가 완료되었습니다.' if form.cleaned_data.get('file') else '자료 링크를 등록했습니다.',extra_tags='upload-success document-upload')
                return redirect(parent_url(kind,obj)+'#work-documents')
    return render(request,'tasks/document_form.html',{'form':form,'parent':obj,'back_url':parent_url(kind,obj)})


def document_parent(obj):
    if obj.task_id:return 'task',obj.task_id
    if obj.work_request_id:return 'request',obj.work_request_id
    return 'project',obj.project_id


@login_required
def document_download(request, pk):
    obj = get_object_or_404(WorkDocument, pk=pk, archived_at__isnull=True)
    kind, parent_id = document_parent(obj)
    parent_for(request.user,kind,parent_id)  # Recheck the current object ACL before touching Drive.
    if not obj.file:
        raise Http404
    try:
        response = FileResponse(obj.file.open('rb'), as_attachment=True, filename=obj.name,
            content_type='application/octet-stream')
    except (FileNotFoundError, RepositoryError):
        return render(request,'tasks/document_error.html',status=503)
    response['Cache-Control']='private, no-store'
    response['X-Content-Type-Options']='nosniff'
    return response


@login_required
@require_POST
@transaction.atomic
def document_archive(request,pk):
    obj=get_object_or_404(WorkDocument.objects.select_for_update(),pk=pk)
    kind,parent_id=document_parent(obj)
    parent=parent_for(request.user,kind,parent_id)
    if not is_company_admin(request.user) and obj.uploader_id != request.user.pk:
        raise PermissionDenied
    reason=request.POST.get('reason','').strip()
    if not reason:
        messages.error(request,'보관 사유를 입력하세요.')
    elif not obj.archived_at:
        obj.archived_at=timezone.now();obj.archive_reason=reason[:500]
        obj.save(update_fields=['archived_at','archive_reason'])
        from .audit import write
        write('update','WorkDocument',obj.pk,obj.name,before={'상태':'사용 중'},after={'상태':'보관','사유':obj.archive_reason},actor=request.user)
    return redirect(parent_url(kind,parent)+'#work-documents')


@login_required
def repository_status(request):
    if not is_company_admin(request.user):raise PermissionDenied
    error='';drive_name=''
    if request.method=='POST':
        try:
            if not configured():raise RepositoryError('Render의 자료 저장소 설정을 확인해 주세요.')
            drive_name=DriveClient().check()
        except RepositoryError as exc:error=str(exc)
    return render(request,'tasks/repository_status.html',{'configured':configured(),'error':error,'drive_name':drive_name})
