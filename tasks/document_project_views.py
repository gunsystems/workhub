from urllib.parse import urlencode
from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError, PermissionDenied
from django.http import JsonResponse
from django.shortcuts import render, redirect, get_object_or_404
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_POST, require_http_methods
from .models import DocumentProject, Project, Employee, DocumentRollout
from .document_permissions import admin_only
from .document_structure import PROJECT_TYPES
from .document_vendors import active_names
from .document_projects import prepare, prepare_close, advance, plan_labels, reconcile_acl
from .permission_inventory import scope
from .sharepoint_probe import ProbeConfig, SharePointProbeError
from .audit import write

STATES={'prepared':'실행 전 확인','running':'처리 중','active':'진행 중','closed':'종료 · 보관 완료','attention':'관리자 확인 필요'}


def visible(user):
    from django.db.models import Q
    rows=DocumentProject.objects.select_related('project__owner__user','creator')
    if user.is_superuser:return rows
    employee=Employee.objects.filter(user=user,is_active=True).first()
    if not employee:return rows.none()
    if employee.position=='executive':return rows
    return rows.filter(Q(project__owner=employee)|Q(participants=employee)).distinct()


class ProjectFolderForm(forms.Form):
    project=forms.ModelChoiceField(label='연결할 프로젝트',queryset=Project.objects.none())
    year=forms.IntegerField(label='연도',min_value=2000,max_value=2099)
    mode=forms.ChoiceField(label='프로젝트 구분',choices=[('customer','고객 프로젝트'),('internal','내부 과제')])
    customer=forms.CharField(label='고객명',max_length=30,required=False)
    vendor=forms.ChoiceField(label='벤더',required=False)
    kind=forms.ChoiceField(label='유형',choices=[('','선택하세요')]+[(s,s) for s in PROJECT_TYPES],required=False)
    internal=forms.CharField(label='내부 과제명',max_length=30,required=False)
    participants=forms.ModelMultipleChoiceField(label='참여자',queryset=Employee.objects.none(),widget=forms.CheckboxSelectMultiple)

    def __init__(self,*args,site,**kwargs):
        super().__init__(*args,**kwargs)
        self.fields['project'].queryset=Project.objects.filter(document_folder__isnull=True).select_related('owner','department')
        self.fields['participants'].queryset=Employee.objects.filter(is_active=True,user__is_active=True).select_related('user','department')
        self.fields['vendor'].choices=[('','선택하세요')]+[(s,s) for s in active_names(site)]
        self.vendors=active_names(site)

    def clean(self):
        from .document_structure import project_name
        values=super().clean()
        if values.get('mode')=='internal':
            values.update(customer='',vendor='',kind='')
        else:values['internal']=''
        if values.get('project') and values.get('participants') is not None and values['project'].owner not in values['participants']:
            self.add_error('participants','프로젝트 총괄 담당자를 포함하세요.')
        if 'year' in values:
            values['folder_name']=project_name(values['year'],customer=values.get('customer',''),vendor=values.get('vendor',''),
                kind=values.get('kind',''),internal=values.get('internal',''),vendors=self.vendors)
        return values


@login_required
@never_cache
@require_GET
def listing(request):
    query=request.GET.get('q','')[:100]
    rows=visible(request.user)
    if query:rows=rows.filter(name__icontains=query)
    return render(request,'tasks/document_projects.html',{'rows':rows,'q':query,'states':STATES})


@admin_only
@never_cache
@require_http_methods(['GET','POST'])
def create(request):
    try:key,site=scope(request.user)
    except SharePointProbeError as exc:
        messages.error(request,str(exc));return redirect('document_permission_setup')
    form=ProjectFolderForm(request.POST if request.method=='POST' else None,site=site,
        initial={'year':timezone.localdate().year,'project':request.GET.get('project')})
    if request.method=='POST' and form.is_valid():
        data=form.cleaned_data
        try:
            row=prepare(request.user,data['project'],{k:data[k] for k in ('year','customer','vendor','kind','internal')},data['participants'])
            return redirect('document_project_detail',pk=row.pk)
        except (ValidationError,SharePointProbeError,DocumentRollout.DoesNotExist) as exc:
            form.add_error(None,' '.join(exc.messages) if isinstance(exc,ValidationError) else
                str(exc) if isinstance(exc,SharePointProbeError) else '초기 적용과 관리자 연결을 먼저 확인하세요.')
        except Exception as exc:
            import logging,traceback
            frame=traceback.extract_tb(exc.__traceback__)[-1]
            logging.getLogger(__name__).error('project_prepare_failed kind=%s location=%s:%s',
                type(exc).__name__,frame.name,frame.lineno)
            form.add_error(None,'생성 검토 중 문제가 발생했습니다. 적용 기록을 확인한 뒤 다시 안내하겠습니다.')
    return render(request,'tasks/document_project_form.html',{'form':form})


@login_required
@never_cache
@require_GET
def detail(request,pk):
    row=get_object_or_404(visible(request.user),pk=pk)
    record=row.data.get('records',{}).get('root')
    folder_url='/work/shared-documents/?'+urlencode({'drive':record['drive'],'folder':record['item']}) if record else ''
    can_request=(request.user.is_active and row.project.owner.user_id==request.user.pk and row.state=='active')
    context={'row':row,'state_label':STATES.get(row.state,'확인 필요'),'labels':plan_labels(row),
        'can_run':request.user.is_superuser and row.administrator_id==request.user.pk and row.state in ('prepared','running') and not row.pending,
        'total':len(row.data['steps']),'folder_url':folder_url,'can_request':can_request,
        'unfinished':row.project.tasks.exclude(status='done').count()}
    return render(request,'tasks/document_project_detail.html',context)


@admin_only
@never_cache
@require_POST
def step(request,pk):
    if request.POST.get('confirmed')!='yes':return JsonResponse({'error':'적용 대상을 먼저 확인하세요.'},status=409)
    try:
        row,waiting=advance(request.user,pk,int(request.POST.get('revision','')))
        return JsonResponse({'state':row.state,'revision':row.revision,'cursor':row.cursor,
            'total':len(row.data['steps']),'waiting':waiting})
    except (DocumentProject.DoesNotExist,ValueError,SharePointProbeError) as exc:
        return JsonResponse({'error':str(exc) if isinstance(exc,SharePointProbeError) else '대상과 실행 단계를 다시 확인하세요.'},status=409)
    except Exception:
        return JsonResponse({'error':'현재 단계의 적용 결과를 확인해야 합니다. 자동 재실행하지 않습니다.'},status=409)


@login_required
@never_cache
@require_POST
def close(request,pk):
    row=get_object_or_404(visible(request.user),pk=pk,state='active')
    if request.POST.get('reviewed')!='yes':
        messages.error(request,'최종 자료와 종료 범위를 먼저 확인하세요.');return redirect('document_project_detail',pk=pk)
    if request.user.is_superuser:
        try:prepare_close(request.user,pk)
        except (SharePointProbeError,DocumentProject.DoesNotExist) as exc:
            messages.error(request,str(exc) if isinstance(exc,SharePointProbeError) else '관리자 연결과 처리 상태를 확인하세요.')
    else:
        if row.project.owner.user_id!=request.user.pk or not request.user.is_active:raise PermissionDenied
        changed=DocumentProject.objects.filter(pk=pk,state='active',close_requested_at__isnull=True).update(
            close_requested_at=timezone.now(),close_requested_by=request.user)
        if changed:write('update','DocumentProject',pk,'프로젝트 종료·보관 요청',after={'폴더':row.name},actor=request.user)
        messages.success(request,'관리자에게 종료·보관 처리를 요청했습니다.')
    return redirect('document_project_detail',pk=pk)


@admin_only
@never_cache
@require_POST
def reconcile(request,pk):
    try:
        reconcile_acl(request.user,pk)
        messages.success(request,'현재 권한이 검토한 내용과 일치함을 확인했습니다. 외부 권한을 다시 변경하지 않았습니다.')
    except (SharePointProbeError,DocumentProject.DoesNotExist) as exc:
        messages.error(request,str(exc) if isinstance(exc,SharePointProbeError) else '현재 처리 기록을 확인하세요.')
    return redirect('document_project_detail',pk=pk)
