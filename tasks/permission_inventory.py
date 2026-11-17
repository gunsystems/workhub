"""Persistent private display snapshot; no caller may use this to authorize files."""
import secrets
import time
from datetime import timedelta
from django.db import transaction
from django.db.models import F
from django.utils import timezone
from .models import DocumentPermissionConnection, PermissionReportSnapshot, PermissionReportName, HiworksDirectoryCache
from .document_permission_api import digest
from .document_permissions import employees
from .sharepoint_probe import ProbeConfig, SharePointProbeError

INTERVAL = timedelta(minutes=15)


def scope(user):
    config = ProbeConfig.from_settings()
    connection = DocumentPermissionConnection.objects.filter(user=user).first()
    if (not connection or str(connection.tenant) != config.tenant_id or str(connection.client) != config.client_id
            or connection.site_url != config.site_url):
        raise SharePointProbeError('관리자 문서 권한 계정을 연결해 주세요.')
    key = digest([user.pk, connection.pk, str(connection.tenant), str(connection.client),
                  str(connection.subject), connection.email, str(connection.connected_at), connection.site_url])
    return key, config.site_url


@transaction.atomic
def current(user):
    key, site = scope(user)
    row, _ = PermissionReportSnapshot.objects.select_for_update().get_or_create(
        user=user, defaults={'scope_key':key, 'site_url':site})
    if row.scope_key != key:
        row.scope_key, row.site_url, row.payload = key, site, {}
        row.completed_at, row.attempted_at = None, None
        row.next_at = row.lease_until = timezone.now()
        row.lease_token, row.error, row.progress = '', '', 0
        row.revision += 1
        row.save()
    return row


def invalidate(site):
    PermissionReportSnapshot.objects.filter(site_url=site).update(next_at=timezone.now(), revision=F('revision')+1)


def directory_names():
    values = {}
    cached = HiworksDirectoryCache.objects.filter(key='access').first()
    if cached:
        for r in cached.rows:
            email, name = str(r.get('email', '')).strip().casefold(), str(r.get('name', '')).strip()
            if email and name:
                values.setdefault(email, set()).add(name)
    for employee in employees():
        values[employee.email.casefold()] = {employee.name}
    # Ambiguous source entries never produce a guessed identity match.
    return {email:next(iter(names)) for email,names in values.items() if len(names)==1}


def person_identity(person):
    return (person.get('email') or person.get('login') or '').strip().casefold()


def normalize_people(payload, site):
    """Names are refreshed locally even while the Microsoft snapshot is retained."""
    import copy
    result = copy.deepcopy(payload)
    names = directory_names()
    aliases = {r.principal_id:r for r in PermissionReportName.objects.filter(site_url=site)}
    unmatched = {}
    def label(person):
        identity = person_identity(person)
        alias = aliases.get(person['id'])
        email = person.get('email', '').casefold()
        if alias and alias.identity == identity:
            email = alias.directory_email.casefold()
        if email in names:
            person['name'] = names[email]
        elif identity:
            unmatched[person['id']] = {'id':person['id'], 'identity':identity,
                                      'name':person['name'], 'email':person.get('email', '')}
    for node in result.get('nodes', []):
        for grant in node['grants']:
            if grant['kind'] == 'person': label(grant)
            for person in grant.get('members', []):
                if person['kind'] == 'person': label(person)
    return result, list(unmatched.values()), names


def build(api, progress=lambda n: None):
    from .permission_report import grants
    from django.urls import reverse
    started = time.monotonic()
    libraries = [{'id':r['id'], 'name':r['name']} for r in api.docs.libraries]
    queue = [(l['id'], 'root', None, 0, l['name']) for l in libraries]
    seen, group_members, nodes = set(), {}, []
    while queue:
        if len(seen) >= 500 or time.monotonic()-started > 240:
            raise SharePointProbeError('현황 조회 범위를 초과했습니다.')
        drive, item, parent, depth, name = queue.pop(0)
        key = drive+'/'+item
        if key in seen or depth > 63:
            raise SharePointProbeError('폴더 구조를 모두 확인하지 못했습니다.')
        seen.add(key)
        target = api.bind(drive, item)
        if 'folder' not in target:
            raise SharePointProbeError('폴더가 변경되었습니다.')
        state = api.state()
        rights = grants(state)
        for grant in rights:
            if grant['kind'] == 'group':
                if grant['id'] not in group_members:
                    # Incomplete or failed member lists fail the whole new snapshot.
                    group_members[grant['id']] = [{'id':r['id'], 'name':r['name'], 'email':r['email'],
                        'login':r.get('login', ''), 'kind':'person' if r['type']==1 else 'other'} for r in api.members(grant['id'])]
                grant['members'] = group_members[grant['id']]
        nodes.append({'key':key, 'parent':parent, 'drive':drive, 'item':item,
            'name':name if item=='root' else target['name'], 'unique':state['unique'],
            'source':state['source']['name'], 'grants':rights, 'at':timezone.now().isoformat(),
            'manage_url':reverse('document_permission_manage',args=[drive,item])+'?edit=1'})
        cursor, cursors = '', set()
        for _ in range(500):
            rows, cursor, _ = api.docs.children_page(drive, '' if item=='root' else item, cursor=cursor)
            for child in rows:
                if 'folder' in child:
                    if len(seen)+len(queue)>=500:
                        raise SharePointProbeError('폴더 수가 많아 전체 현황을 확인하지 못했습니다.')
                    queue.append((drive,child['id'],key,depth+1,child['name']))
            if not cursor: break
            if cursor in cursors or time.monotonic()-started > 240:
                raise SharePointProbeError('폴더 목록을 모두 확인하지 못했습니다.')
            cursors.add(cursor)
        else:
            raise SharePointProbeError('폴더 목록을 모두 확인하지 못했습니다.')
        progress(len(nodes))
    return {'libraries':libraries, 'nodes':nodes}


def synchronize_due():
    """One job, isolated worker loop. Leased/fenced across Gunicorn processes."""
    now = timezone.now()
    row = PermissionReportSnapshot.objects.filter(next_at__lte=now, lease_until__lte=now,
        user__is_active=True,user__is_superuser=True).select_related('user').order_by('next_at').first()
    if not row: return False
    token = secrets.token_hex(16)
    if not PermissionReportSnapshot.objects.filter(pk=row.pk, lease_until__lte=now).update(
            lease_token=token,lease_until=now+timedelta(minutes=10),attempted_at=now,progress=0): return False
    fence = PermissionReportSnapshot.objects.filter(pk=row.pk,lease_token=token,scope_key=row.scope_key)
    try:
        key, site = scope(row.user)
        if key != row.scope_key:
            # Discard a disconnected/replaced identity's old snapshot.
            fence.update(payload={},completed_at=None,next_at=now+INTERVAL,error='관리자 연결이 변경되었습니다.')
            return False
        from .group_permission_api import GroupPermissionClient
        api = GroupPermissionClient(row.user)
        if scope(row.user)[0] != key: raise SharePointProbeError('연결 변경')
        def progress(n):
            if not fence.filter(revision=row.revision).update(progress=n):
                raise SharePointProbeError('조회 중 설정이 변경되었습니다.')
        payload = build(api, progress)
        if scope(row.user)[0] != key: raise SharePointProbeError('연결 변경')
        if not fence.filter(revision=row.revision).update(payload=payload,completed_at=timezone.now(),
                next_at=timezone.now()+INTERVAL,error='',progress=len(payload['nodes'])):
            return False
        return True
    except Exception:
        # Never replace a complete snapshot with partial/empty/error results.
        fence.filter(revision=row.revision).update(error='최신 정보 확인 필요 · 기존 현황을 표시합니다.',
            next_at=timezone.now()+timedelta(minutes=2))
        return False
    finally:
        fence.update(lease_token='',lease_until=timezone.now())
