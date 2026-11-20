"""Administrator structure preview, vendor settings and guarded rollout."""
from django.core.exceptions import ValidationError
from django.shortcuts import render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET
from django.views.decorators.http import require_POST
from django.http import JsonResponse, Http404
from django.shortcuts import redirect
from .models import DocumentRollout
from .document_permissions import admin_only
from .document_structure import (POLICY_VERSION, GROUPS, COMMON_ROOT_GRANTS,
    VENDORS, fixed_folders, inventory_paths, preview)
from .models import PermissionReportSnapshot
from .permission_inventory import scope
from .sharepoint_probe import SharePointProbeError

LIBRARIES = {'common':'전사공통', 'documents':'문서', 'projects':'프로젝트 공동작업'}
ROLES = {'read':'읽기', 'write':'편집', 'write_no_delete':'편집(삭제불가)', 'manage':'관리'}


@admin_only
@never_cache
@require_GET
def planned_structure(request):
    context={'version':POLICY_VERSION, 'vendors':VENDORS, 'total':len(fixed_folders()),
             'common_grants':' · '.join(f'{GROUPS[g]} {ROLES[r]}' for g,r in COMMON_ROOT_GRANTS)}
    try:
        key,site=scope(request.user)
        snapshot=PermissionReportSnapshot.objects.filter(user=request.user,scope_key=key,
            site_url=site,completed_at__isnull=False).first()
        if not snapshot or not snapshot.payload:
            raise SharePointProbeError('폴더 권한 현황을 먼저 동기화하세요.')
        context.update(checked_at=snapshot.completed_at, inventory_error=snapshot.error,
                       libraries=snapshot.payload['libraries'])
        rows=preview(inventory_paths(snapshot.payload))
        context['rows']=[{'path':LIBRARIES[r['folder'].library]+'/'+ '/'.join(r['folder'].path),
            'existing':r['action']=='existing_review', 'unique':r['folder'].unique,
            'grants':' · '.join(f'{GROUPS[g]} {ROLES[v]}' for g,v in r['folder'].grants)} for r in rows]
        context['create_count']=sum(not r['existing'] for r in context['rows'])
        context['existing_count']=len(rows)-context['create_count']
    except (SharePointProbeError, ValidationError, KeyError, TypeError, ValueError) as exc:
        context['error']=str(exc) if isinstance(exc,SharePointProbeError) else '현황 경로를 확인하지 못했습니다. 다시 동기화하세요.'
    return render(request,'tasks/document_structure_plan.html',context)


def owned_run(user, pk):
    key,site=scope(user)
    run=DocumentRollout.objects.filter(pk=pk,owner=user,site_url=site,scope_key=key).first()
    if not run: raise Http404
    return run


@admin_only
@never_cache
@require_POST
def prepare_rollout(request):
    from .document_rollout import prepare
    from django.contrib import messages
    try:
        run=prepare(request.user)
    except SharePointProbeError as exc:
        messages.error(request,str(exc))
        return redirect('document_structure_plan')
    return redirect('document_rollout_detail',pk=run.pk)


@admin_only
@never_cache
@require_GET
def rollout_detail(request,pk):
    from .permission_inventory import named_members
    run=owned_run(request.user,pk)
    groups=[]
    for role,group in run.data['groups'].items():
        groups.append({'role':GROUPS[role],'name':group['name'],
            'members':named_members(group['members'],run.site_url)})
    from .document_rollout import NEW_GROUPS
    for role,name in NEW_GROUPS.items():
        if role not in run.data['groups']:
            groups.append({'role':GROUPS[role],'name':name,'new':True,
                           'executive':role=='executives'})
    return render(request,'tasks/document_rollout.html',{'run':run,'groups':groups,
        'total':len(run.data['steps']),'results':run.data['results'][-12:],
        'state_label':{'prepared':'적용 검토 대기','running':'적용 중','attention':'결과 확인 필요','done':'적용 완료'}.get(run.state,'확인 필요'),
        'vendor_phase':run.data.get('phase')=='vendor',
        'can_run':run.state in ('prepared','running') and not run.pending})


@admin_only
@never_cache
@require_POST
def rollout_step(request,pk):
    from .document_rollout import advance
    run=owned_run(request.user,pk)
    if request.POST.get('confirmed')!='yes':
        return JsonResponse({'error':'대상 그룹과 적용 범위를 먼저 확인하세요.'},status=409)
    try:
        revision=int(request.POST.get('revision',''))
        run=advance(request.user,run.pk,revision)
    except (ValueError,SharePointProbeError) as exc:
        return JsonResponse({'error':str(exc) if isinstance(exc,SharePointProbeError) else '적용 단계를 다시 확인하세요.'},status=409)
    except Exception:
        return JsonResponse({'error':'현재 단계의 적용 결과를 확인해야 합니다. 중복 실행하지 않고 중단했습니다.'},status=409)
    return JsonResponse({'state':run.state,'revision':run.revision,'cursor':run.cursor,
        'total':len(run.data['steps']),'last':run.data['results'][-1]})


@admin_only
@never_cache
@require_GET
def vendor_settings(request):
    from .document_vendors import catalog
    key,site=scope(request.user)
    run=DocumentRollout.objects.filter(site_url=site,owner=request.user,scope_key=key).first()
    return render(request,'tasks/document_vendors.html',{'vendors':catalog(site),'run':run,
        'can_add':bool(run and run.state=='done')})


@admin_only
@never_cache
@require_POST
def vendor_change(request):
    from .document_vendors import queue_vendor,toggle
    from django.contrib import messages
    try:
        if request.POST.get('action')=='add':
            run=queue_vendor(request.user,request.POST.get('name',''))
            return redirect('document_rollout_detail',pk=run.pk)
        if request.POST.get('action')=='active':
            if request.POST.get('active') not in ('yes','no'):
                raise ValidationError('변경할 상태를 다시 선택하세요.')
            toggle(request.user,request.POST.get('name',''),request.POST.get('active')=='yes')
            messages.success(request,'벤더 선택 목록을 변경했습니다. 기존 폴더와 자료는 유지됩니다.')
        else:raise ValidationError('변경할 항목을 확인하세요.')
    except (ValidationError,SharePointProbeError,DocumentRollout.DoesNotExist) as exc:
        messages.error(request,' '.join(exc.messages) if isinstance(exc,ValidationError) else
            str(exc) if isinstance(exc,SharePointProbeError) else '초기 적용과 관리자 연결을 확인하세요.')
    return redirect('document_vendor_settings')
