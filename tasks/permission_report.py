"""Read-only administrator folder ACL inventory; no persisted authorization cache."""
from cryptography.fernet import InvalidToken
from django.http import Http404, JsonResponse
from django.shortcuts import render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_POST
from .document_permissions import admin_only, employees
from .group_permission_api import GroupPermissionClient
from .sharepoint_probe import SharePointProbeError


def role_label(row):
    kinds = {kind for _, kind in row['roles'] if kind != 1}
    if 0 in kinds:
        return '사용자 정의 (별도 확인)'
    if 5 in kinds:
        return '관리'
    if kinds & {3, 6}:
        return '편집'
    if kinds == {2}:
        return '보기'
    return '사용자 정의 (별도 확인)'


def grants(state):
    # Include protected individual principals too; Limited Access is traversal,
    # not permission to read the contents of every folder on the path.
    return [{'id': r['id'], 'name': r['name'], 'email': r['email'],
             'kind': 'group' if r['type'] == 8 else 'person' if r['type'] == 1 else 'other',
             'role': role_label(r)} for r in state['rows'] if not r['limited']]


@admin_only
@never_cache
@require_GET
def overview(request):
    context = {}
    try:
        from .permission_inventory import current
        context['initial'] = display_state(current(request.user))
    except SharePointProbeError as exc:
        context['error'] = str(exc)
    return render(request, 'tasks/permission_report.html', context)


def display_state(row, known=''):
    from .permission_inventory import normalize_people
    at = row.completed_at.isoformat() if row.completed_at else ''
    now = timezone.now()
    result = {'at':at,'pending':row.lease_until>now or row.next_at<=now,
              'progress':row.progress,'error':row.error,'payload':None}
    if not known or known != at:
        payload, unmatched, directory = normalize_people(row.payload, row.site_url)
        result.update(payload=payload,unmatched=unmatched,
            directory=[{'email':email,'name':name} for email,name in sorted(directory.items(),key=lambda r:r[1])])
    return result


@admin_only
@never_cache
@require_GET
def status(request):
    from .permission_inventory import current
    try:
        return JsonResponse(display_state(current(request.user),request.GET.get('known','')))
    except SharePointProbeError:
        return JsonResponse({'error':'관리자 문서 연결을 확인해 주세요.'},status=400)


@admin_only
@never_cache
@require_POST
def refresh(request):
    from .permission_inventory import current
    from .models import PermissionReportSnapshot
    try:
        row = current(request.user)
        PermissionReportSnapshot.objects.filter(pk=row.pk).update(next_at=timezone.now(),error='')
        row.refresh_from_db()
        return JsonResponse(display_state(row),status=202)
    except SharePointProbeError:
        return JsonResponse({'error':'관리자 문서 연결을 확인해 주세요.'},status=400)


@admin_only
@never_cache
@require_POST
def names(request):
    from .permission_inventory import current, normalize_people
    from .models import PermissionReportName
    from .audit import write
    try:
        row = current(request.user)
        _, unmatched, directory = normalize_people(row.payload,row.site_url)
        selected = next((r for r in unmatched if str(r['id'])==request.POST.get('principal')),None)
        email = request.POST.get('email','').strip().casefold()
        if not selected or email not in directory:
            raise SharePointProbeError('이름을 연결할 직원과 Microsoft 계정을 다시 확인하세요.')
        PermissionReportName.objects.update_or_create(site_url=row.site_url,principal_id=selected['id'],
            defaults={'identity':selected['identity'],'directory_email':email})
        write('update','PermissionReportName',selected['id'],'문서 현황 직원 이름 연결',
              after={'Microsoft 계정':selected['identity'],'직원 이메일':email},actor=request.user)
        return JsonResponse(display_state(row))
    except SharePointProbeError:
        return JsonResponse({'error':'이름을 연결할 직원과 계정을 다시 확인하세요.'},status=400)


@admin_only
@never_cache
@require_GET
def node(request):
    """One folder + one bounded child page per request, fresh delegated reads."""
    try:
        api = GroupPermissionClient(request.user)
        drive, item = request.GET.get('drive', ''), request.GET.get('item', 'root')
        target = api.bind(drive, item)
        if 'folder' not in target:
            raise Http404
        state = api.state()
        rows, cursor, _ = api.docs.children_page(drive, '' if item == 'root' else item,
                                               cursor=request.GET.get('cursor', ''))
        name = target['name']
        if item == 'root':
            name = next(r['name'] for r in api.docs.libraries if r['id'] == drive)
        return JsonResponse({'name': name, 'unique': state['unique'],
            'source': state['source']['name'], 'grants': grants(state),
            'children': [{'id': r['id'], 'name': r['name']} for r in rows if 'folder' in r],
            'cursor': cursor, 'at': timezone.now().isoformat(),
            'manage_url': reverse('document_permission_manage', args=[drive, item]) + '?edit=1'})
    except (SharePointProbeError, InvalidToken, ValueError, TypeError):
        return JsonResponse({'error': '폴더 권한을 확인하지 못했습니다. 관리자 연결을 확인한 뒤 다시 조회하세요.'}, status=400)


@admin_only
@never_cache
@require_GET
def members(request, group):
    try:
        api = GroupPermissionClient(request.user)
        # A numeric ID is not permission: validate against this selected site's catalog.
        if not any(r['id'] == group for r in api.groups()):
            raise Http404
        from .permission_inventory import named_members
        values = named_members(api.members(group),api.config.site_url)
        return JsonResponse({'members': [{'name': r['name'],
            'email': r['email'], 'kind': 'person' if r['type'] == 1 else 'other'} for r in values],
            'at': timezone.now().isoformat()})
    except (SharePointProbeError, ValueError, TypeError):
        return JsonResponse({'error': '그룹 구성원을 확인하지 못했습니다. 다시 조회하세요.'}, status=400)
