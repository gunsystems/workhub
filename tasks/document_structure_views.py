"""Read-only administrator rollout preview. No Microsoft write endpoint."""
from django.core.exceptions import ValidationError
from django.shortcuts import render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET
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
