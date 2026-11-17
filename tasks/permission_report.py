"""Read-only administrator folder ACL inventory; no persisted authorization cache."""
from cryptography.fernet import InvalidToken
from django.http import Http404, JsonResponse
from django.shortcuts import render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET
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
        api = GroupPermissionClient(request.user)
        context['libraries'] = [{'id': r['id'], 'name': r['name']} for r in api.docs.libraries]
    except SharePointProbeError as exc:
        context['error'] = str(exc)
    return render(request, 'tasks/permission_report.html', context)


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
        directory = {s.email.casefold(): s.name for s in employees()}
        values = api.members(group)
        return JsonResponse({'members': [{'name': directory.get(r['email'], r['name']),
            'email': r['email'], 'kind': 'person' if r['type'] == 1 else 'other'} for r in values],
            'at': timezone.now().isoformat()})
    except (SharePointProbeError, ValueError, TypeError):
        return JsonResponse({'error': '그룹 구성원을 확인하지 못했습니다. 다시 조회하세요.'}, status=400)
