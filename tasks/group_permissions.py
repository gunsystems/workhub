"""Administrator workspace for SharePoint group membership and inherited ACLs."""
import secrets
from datetime import timedelta
from urllib.parse import urlencode
from cryptography.fernet import InvalidToken
from django.contrib import messages
from django.db import IntegrityError, transaction
from django.http import JsonResponse, Http404
from django.shortcuts import render, redirect
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_POST
from .document_permissions import admin_only, employees, safe_snapshot
from .document_permission_api import digest
from .group_permission_api import GroupPermissionClient, entries
from .models import DocumentAccessGroup, DocumentPermissionLease
from .sharepoint_probe import SharePointProbeError
from .audit import write

ROLE_NAMES = {'none': '연결 없음', 'read': '보기', 'write': '편집', 'manage': '관리'}


def group_catalog(api):
    groups = api.groups()
    managed = {r.principal_id: r.name for r in DocumentAccessGroup.objects.filter(site_url=api.config.site_url)}
    for group in groups:
        group['managed'] = managed.get(group['id']) == group['name']
    return groups


def role_of(row):
    if not row or row['limited']:
        return 'none'
    kinds = {kind for _, kind in row['roles']}
    if row['protected'] or 5 in kinds:
        return 'manage'
    if kinds & {3, 6}:
        return 'write'
    if kinds == {2}:
        return 'read'
    return 'custom'


def overview(request, drive='', item='root'):
    """GET full page or one server-rendered panel, no ACL writes."""
    context = {'drive': drive, 'item': item, 'staff': list(employees()), 'tab': request.GET.get('tab', 'folders')}
    context['departments'] = sorted({str(s.department) for s in context['staff']})
    if context['tab'] not in ('folders', 'groups', 'people'):
        raise Http404
    try:
        api = GroupPermissionClient(request.user)
        context['libraries'] = api.docs.libraries
        context['groups'] = groups = group_catalog(api)
        drive = drive or request.GET.get('drive') or (api.docs.libraries[0]['id'] if api.docs.libraries else '')
        context['drive'] = drive
        context['item'] = item = request.GET.get('folder', item)
        if not drive:
            raise SharePointProbeError('관리할 문서함이 없습니다.')
        context['target'] = api.bind(drive, item)
        if 'root' in context['target'] or item == 'root':
            context['target'] = {**context['target'], 'name': next(
                (library['name'] for library in api.docs.libraries if library['id'] == drive), '문서함 전체')}
        context['snapshot'] = state = api.state()
        state['direct_people'] = [r for r in state['rows'] if r['type'] == 1 and not r['protected'] and not r['limited']]
        context['personal_count'] = len(state['direct_people'])
        context['checked_at'] = timezone.now()
        # Do not pretend that group member counts are distinct effective user counts.
        context['group_count'] = sum(r['type'] == 8 and not r['limited'] for r in state['rows'])
        context['editable_groups'] = []
        for group in groups:
            row = next((r for r in state['rows'] if r['id'] == group['id'] and r['type'] == 8), None)
            value = role_of(row)
            context['editable_groups'].append({**group, 'role': value,
                'locked': not state['unique'] or bool(row and (row['protected'] or row['limited'] or value == 'custom')),
                'label': ROLE_NAMES.get(value, '사용자 정의')})
        if context['tab'] == 'groups':
            selected = request.GET.get('group', '')
            group = next((r for r in groups if str(r['id']) == selected), None)
            context['selected_group'] = group
            if group:
                if not group['managed']:
                    try:
                        api.check_membership_group(group['id'])
                        context['can_register_group'] = True
                    except SharePointProbeError:
                        context['can_register_group'] = False
                members = api.members(group['id'])
                context['members'] = members
                emails = {r['email'] for r in members if r['type'] == 1}
                context['available_staff'] = [s for s in context['staff'] if s.email.casefold() not in emails]
                for member in members:
                    member['locked'] = member['site_admin'] or member['id'] == int(api.identity['Id']) or member['type'] != 1
        if context['tab'] == 'people':
            employee = next((s for s in context['staff'] if str(s.pk) == request.GET.get('employee')), None)
            context['selected_employee'] = employee
            if employee:
                # Fresh server-resolved identity, never an email supplied by browser.
                # ensureuser would be a write, so use siteusers lookup instead.
                login = 'i:0#.f|membership|' + employee.email.casefold()
                query = urlencode({'@u': "'" + login.replace("'", "''") + "'"})
                value = api.call(api.resource + '/getusereffectivepermissions(@u)?' + query)
                permission_bits = value if 'Low' in value else value.get('GetUserEffectivePermissions')
                if not isinstance(permission_bits, dict) or 'Low' not in permission_bits:
                    raise SharePointProbeError('직원의 실제 접근 권한을 확인하지 못했습니다.')
                bits = int(permission_bits['Low'])
                context['effective_role'] = '관리' if bits & (1 << 25) else '편집' if bits & 4 else '보기' if bits & 1 else '접근 불가'
                context['employee_groups'] = api.employee_groups(employee.email.casefold())
    except (SharePointProbeError, InvalidToken, ValueError, TypeError) as exc:
        context['error'] = str(exc) if isinstance(exc, SharePointProbeError) else '문서 권한을 다시 조회해 주세요.'
    template = 'tasks/group_permission_panel.html' if request.GET.get('panel') == '1' else 'tasks/group_permissions.html'
    return render(request, template, context)


@admin_only
@never_cache
@require_GET
def workspace(request):
    return overview(request)


@admin_only
@never_cache
@require_GET
def target(request, drive, item):
    return overview(request, drive, item)


@admin_only
@never_cache
@require_GET
def tree(request):
    try:
        api = GroupPermissionClient(request.user)
        drive, folder = request.GET.get('drive', ''), request.GET.get('folder', '')
        rows, cursor, _ = api.docs.children_page(drive, '' if folder == 'root' else folder,
                                               cursor=request.GET.get('cursor', ''))
        return JsonResponse({'rows': [{'id': r['id'], 'name': r['name']} for r in rows if 'folder' in r],
                             'cursor': cursor})
    except (SharePointProbeError, InvalidToken):
        return JsonResponse({'error': '폴더 목록을 불러오지 못했습니다. 다시 조회해 주세요.'}, status=400)


def return_url(drive, item):
    return reverse('document_permission_manage', args=[drive, item])


@admin_only
@never_cache
@require_POST
def review(request, drive, item):
    context = {'drive': drive, 'item': item}
    try:
        api = GroupPermissionClient(request.user)
        target_row = api.bind(drive, item)
        before = api.state()
        action = request.POST.get('action', '')
        changes, summary = [], []
        catalog = group_catalog(api)
        payload = {'action': action, 'drive': drive, 'item': item, 'before': before,
            'connection': str(api.connection.connected_at), 'site': api.config.site_url,
            'user': request.user.pk, 'name': target_row['name'], 'at': timezone.now().timestamp()}
        if action == 'groups':
            if not before['unique']:
                raise SharePointProbeError('상속 중인 폴더는 상속 원본에서 변경하거나 먼저 별도 설정하세요.')
            for group in catalog:
                key = 'group_' + str(group['id'])
                if key not in request.POST:
                    continue
                old = next((r for r in before['rows'] if r['id'] == group['id']), None)
                role = request.POST[key]
                old_role = role_of(old)
                if role not in ROLE_NAMES or (old and (old['protected'] or old['limited'] or old_role == 'custom')):
                    raise SharePointProbeError('변경할 그룹 권한을 확인해 주세요.')
                if role != old_role:
                    changes.append({'id': group['id'], 'role': role})
                    summary.append(f"{group['name']} · {ROLE_NAMES[old_role]} → {ROLE_NAMES[role]}")
            if not changes:
                raise SharePointProbeError('변경한 권한이 없습니다.')
            payload['changes'] = changes
        elif action in ('break', 'reset'):
            if (action == 'break') == before['unique']:
                raise SharePointProbeError('상속 상태가 변경되었습니다. 다시 조회하세요.')
            summary = ['현재 상위 권한을 복사하여 이 폴더만 별도 설정' if action == 'break' else
                       '이 폴더의 개별 설정을 해제하고 현재 상위 권한으로 복원']
            if action == 'reset':
                payload['parent'] = api.parent_state()
        elif action == 'create_group':
            name = request.POST.get('name', '').strip()
            if not name or len(name) > 80 or any(c in name for c in '<>"\\/\r\n'):
                raise SharePointProbeError('그룹 이름을 80자 이내로 입력하세요.')
            payload['group_name'] = 'Workhub_' + name
            if any(g['name'].casefold() == payload['group_name'].casefold() for g in catalog):
                raise SharePointProbeError('같은 이름의 그룹이 있습니다.')
            summary = [payload['group_name'] + ' · 빈 그룹 생성 (문서 접근 권한은 별도 연결)']
        elif action == 'register_group':
            group = next((g for g in catalog if str(g['id']) == request.POST.get('group')), None)
            if not group or group['managed']:
                raise SharePointProbeError('관리할 기존 그룹을 다시 선택하세요.')
            api.check_membership_group(group['id'])
            payload['group'] = group
            summary = [group['name'] + ' · Workhub에서 구성원 관리 연결']
        elif action == 'members':
            group = next((g for g in catalog if str(g['id']) == request.POST.get('group') and g['managed']), None)
            if not group:
                raise SharePointProbeError('먼저 이 그룹의 구성원 관리를 연결해 주세요.')
            staff = {str(s.pk): s for s in employees()}
            add_ids = request.POST.getlist('add')
            if any(pk not in staff for pk in add_ids) or len(set(add_ids)) != len(add_ids):
                raise SharePointProbeError('추가할 직원을 다시 선택하세요.')
            members = api.members(group['id'])
            try:
                remove_ids = [int(v) for v in request.POST.getlist('remove')]
            except ValueError:
                raise SharePointProbeError('제외할 직원을 다시 선택하세요.') from None
            if len(set(remove_ids)) != len(remove_ids):
                raise SharePointProbeError('대상이 중복됩니다.')
            for pid in remove_ids:
                person = next((m for m in members if m['id'] == pid), None)
                if not person or person['site_admin'] or person['id'] == int(api.identity['Id']) or person['type'] != 1:
                    raise SharePointProbeError('제외할 구성원을 확인해 주세요. 관리자 접근은 유지합니다.')
            additions = [{'employee': staff[pk].pk, 'email': staff[pk].email.casefold(), 'name': staff[pk].name} for pk in add_ids]
            if not additions and not remove_ids:
                raise SharePointProbeError('추가하거나 제외할 직원을 선택하세요.')
            payload.update(group=group, members=members, additions=additions, removals=remove_ids)
            summary = [r['name'] + ' · 그룹에 추가' for r in additions] + [
                m['name'] + ' · 그룹에서 제외' for m in members if m['id'] in remove_ids]
        else:
            raise SharePointProbeError('변경 항목을 확인해 주세요.')
        nonce = secrets.token_urlsafe(24)
        request.session['document_group_preview'] = {nonce: payload}
        context.update(preview=payload, summary=summary, nonce=nonce)
    except (SharePointProbeError, InvalidToken, KeyError, ValueError, TypeError) as exc:
        messages.error(request, str(exc) if isinstance(exc, SharePointProbeError) else '관리자 연결을 확인하세요.')
        return redirect(return_url(drive, item))
    return render(request, 'tasks/group_permission_review.html', context)


@admin_only
@never_cache
@require_POST
def apply(request, drive, item):
    payload = request.session.pop('document_group_preview', {}).get(request.POST.get('nonce', ''), {})
    lease, started = None, False
    try:
        reason = request.POST.get('reason', '').strip()
        if (payload.get('user') != request.user.pk or payload.get('drive') != drive or payload.get('item') != item
                or request.POST.get('confirmed') != 'yes' or not reason or len(reason) > 500
                or not 0 <= timezone.now().timestamp() - payload.get('at', 0) < 600):
            raise SharePointProbeError('변경 내용과 사유를 확인하고 다시 검토해 주세요.')
        api = GroupPermissionClient(request.user)
        api.bind(drive, item)
        if payload['site'] != api.config.site_url or payload['connection'] != str(api.connection.connected_at):
            raise SharePointProbeError('관리자 연결이 변경되었습니다. 다시 검토하세요.')
        # A site-wide lease serializes ancestor, child and group writes from Workhub.
        # External SharePoint writes are additionally checked before each mutation.
        key = digest(['document-permission-site', api.config.site_url])
        try:
            with transaction.atomic():
                DocumentPermissionLease.objects.filter(target=key, expires_at__lt=timezone.now()).delete()
                lease = DocumentPermissionLease.objects.create(target=key, expires_at=timezone.now()+timedelta(minutes=15))
        except IntegrityError:
            raise SharePointProbeError('다른 문서 권한 변경이 진행 중입니다. 잠시 후 다시 검토하세요.') from None
        action = payload['action']
        if digest(api.state()) != digest(payload['before']):
            raise SharePointProbeError('권한이 변경되었습니다. 다시 검토하세요.')
        if action in ('members', 'register_group'):
            api.check_membership_group(payload['group']['id'])
        if action == 'register_group':
            catalog = group_catalog(api)
            if not any(g == payload['group'] for g in catalog):
                raise SharePointProbeError('그룹 정보가 변경되었습니다. 다시 검토하세요.')
        if action == 'members':
            live = {str(s.pk): s.email.casefold() for s in employees()}
            if any(live.get(str(r['employee'])) != r['email'] for r in payload['additions']):
                raise SharePointProbeError('직원 정보가 변경되었습니다. 다시 검토하세요.')
            catalog = group_catalog(api)
            if not any(g == payload['group'] and g['managed'] for g in catalog):
                raise SharePointProbeError('그룹 정보가 변경되었습니다. 다시 검토하세요.')
        write('permission_started', 'SharePointGroupPermission', item, '문서 권한 변경 시작 · '+payload['name'],
            before={'권한': safe_snapshot(payload['before'])}, after={'작업': action, '사유': reason}, actor=request.user)
        started = True
        def observe(label, value):
            if not DocumentPermissionLease.objects.filter(pk=lease.pk, expires_at__gt=timezone.now()).update(
                    expires_at=timezone.now()+timedelta(minutes=15)):
                raise SharePointProbeError('처리 잠금이 만료되었습니다. 실제 권한을 다시 확인하세요.')
            write('update', 'SharePointGroupPermission', item, label+' · '+payload['name'],
                after={'결과': value, '사유': reason}, actor=request.user)
        if action == 'register_group':
            group = payload['group']
            DocumentAccessGroup.objects.update_or_create(site_url=api.config.site_url,
                principal_id=group['id'], defaults={'name': group['name']})
            write('update', 'SharePointGroupPermission', group['id'], '문서 그룹 구성원 관리 연결 · '+group['name'],
                after={'그룹': group['name'], '사유': reason}, actor=request.user)
        elif action == 'members':
            result = api.group_change(payload['group']['id'], payload['members'],
                [r['email'] for r in payload['additions']], payload['removals'], observe)
            write('update', 'SharePointGroupPermission', payload['group']['id'], '문서 그룹 구성원 변경 · '+payload['group']['name'],
                before={'구성원': payload['members']}, after={'구성원': result, '사유': reason}, actor=request.user)
        elif action == 'create_group':
            gid = api.create_group(payload['group_name'])
            # If persistence fails, leave the real group visible as read-only; never
            # silently adopt unrelated groups or retry creation on an uncertain result.
            DocumentAccessGroup.objects.create(site_url=api.config.site_url, principal_id=gid, name=payload['group_name'])
            write('create', 'SharePointGroupPermission', gid, '문서 그룹 생성 · '+payload['group_name'],
                  after={'그룹': payload['group_name'], '사유': reason}, actor=request.user)
        else:
            result = api.change(payload['before'], action, payload.get('changes', []), observe,
                                parent=payload.get('parent'))
            write('update', 'SharePointGroupPermission', item, '문서 권한 변경 완료 · '+payload['name'],
                before=safe_snapshot(payload['before']), after={**safe_snapshot(result), '사유': reason}, actor=request.user)
        messages.success(request, '변경 결과를 확인하고 기록했습니다.')
    except (SharePointProbeError, InvalidToken, IntegrityError, KeyError, ValueError, TypeError) as exc:
        messages.error(request, str(exc) if isinstance(exc, SharePointProbeError) else '처리 결과를 다시 확인해 주세요.')
        if started:
            write('permission_attention', 'SharePointGroupPermission', item, '문서 권한 결과 확인 필요',
                  after={'결과': '일부 변경이 반영되었을 수 있습니다. 실제 상태 재조회 필요'}, actor=request.user)
    finally:
        if lease:
            DocumentPermissionLease.objects.filter(pk=lease.pk).delete()
    url = return_url(drive, item)
    if payload.get('action') in ('members', 'create_group', 'register_group'):
        url += '?' + urlencode({'tab': 'groups', 'group': (payload.get('group') or {}).get('id', '')})
    return redirect(url)
