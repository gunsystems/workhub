"""Selected-site delegated ACL management. No app-only token or local ACL substitute."""
import hashlib
import json
import uuid
from urllib.parse import urlsplit
import requests
from cryptography.fernet import InvalidToken
from django.core.exceptions import PermissionDenied
from .shared_documents import UserDocuments, cipher, token_request, GRAPH, segment
from .sharepoint_probe import ProbeConfig, SharePointProbeError, _json_request
from .models import DocumentPermissionConnection

MANAGE_PERMISSIONS = 1 << 25


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=True).encode()).hexdigest()


class PermissionClient:
    def __init__(self, user):
        if not user.is_authenticated or not user.is_active or not user.is_superuser:
            raise PermissionDenied
        self.user = user
        self.config = ProbeConfig.from_settings()
        connection = DocumentPermissionConnection.objects.filter(user=user).first()
        if (not connection or str(connection.tenant) != self.config.tenant_id
                or str(connection.client) != self.config.client_id or connection.site_url != self.config.site_url):
            raise SharePointProbeError('관리자 문서 권한 계정을 연결해 주세요.')
        self.connection = connection
        self.docs = UserDocuments(user, permission_connection=connection)
        try:
            refresh = cipher().decrypt(connection.encrypted_refresh_token.encode()).decode()
        except (InvalidToken, ValueError):
            raise SharePointProbeError('관리자 문서 권한 계정을 다시 연결해 주세요.') from None
        auth = token_request(self.config, {'grant_type': 'refresh_token', 'refresh_token': refresh,
            'scope': self.scope(self.config)})
        self.token = auth.get('access_token')
        if not self.token:
            raise SharePointProbeError('관리자 문서 권한 계정을 다시 연결해 주세요.')
        if auth.get('refresh_token'):
            connection.encrypted_refresh_token = cipher().encrypt(auth['refresh_token'].encode()).decode()
            connection.save(update_fields=['encrypted_refresh_token'])
        self.identity = self.call('currentuser?$select=Id,Email,LoginName,IsSiteAdmin,Title')
        candidates = {str(self.identity.get('Email', '')).casefold(),
                      str(self.identity.get('LoginName', '')).rsplit('|', 1)[-1].casefold()}
        if connection.email.casefold() not in candidates or not self.identity.get('Id'):
            raise SharePointProbeError('연결 계정의 문서 관리 권한을 확인하지 못했습니다.')

    @staticmethod
    def scope(config):
        return 'https://' + urlsplit(config.site_url).hostname + '/Sites.Selected'

    def call(self, suffix, method='GET', data=None):
        # Every path is built internally from numeric IDs/GUIDs. Never accept a URL from the browser.
        url = self.config.site_url + '/_api/web/' + suffix
        headers = {'Authorization': 'Bearer ' + self.token, 'Accept': 'application/json;odata=nometadata'}
        try:
            with requests.request(method, url, headers=headers, json=data, timeout=(10, 20),
                                  allow_redirects=False) as response:
                if response.status_code not in (200, 201, 204):
                    raise SharePointProbeError({401: '관리자 문서 권한 계정을 다시 연결해 주세요.',
                        403: '연결한 Microsoft 계정에 권한 관리 권한이 없습니다.',
                        404: '대상 문서나 폴더를 찾을 수 없습니다.',
                        429: '요청이 많습니다. 잠시 후 현재 권한을 다시 확인하세요.'}.get(response.status_code,
                        '권한 처리 결과를 확인하지 못했습니다. 현재 권한을 다시 조회하세요.'))
                if response.status_code == 204 or not response.content:
                    return {}
                if len(response.content) > 1_000_000:
                    raise ValueError
                value = response.json()
                if not isinstance(value, dict):
                    raise ValueError
                result = value.get('d', value)
                if not isinstance(result, dict):
                    raise ValueError
                return result
        except (requests.RequestException, ValueError, TypeError):
            raise SharePointProbeError('권한 처리 결과를 확인하지 못했습니다. 현재 권한을 다시 조회하세요.') from None

    def target(self, drive, item):
        row = self.docs.item(drive, item)
        if 'root' in row:
            raise SharePointProbeError('문서함 전체 권한은 저장소 관리에서 설정하세요. 폴더나 파일을 선택해 주세요.')
        raw = _json_request(f'{GRAPH}/drives/{segment(drive)}/items/{segment(item)}?$select=sharepointIds',
                            token=self.docs.token)
        ids = raw.get('sharepointIds') or {}
        try:
            list_id = str(uuid.UUID(ids['listId']))
            item_id = int(ids['listItemId'])
            if item_id < 1:
                raise ValueError
        except (ValueError, TypeError, KeyError):
            raise SharePointProbeError('문서의 권한 관리 위치를 확인하지 못했습니다.') from None
        self.resource = f"lists(guid'{list_id}')/items({item_id})"
        return row

    def snapshot(self):
        meta = self.call(self.resource + '?$select=Id,HasUniqueRoleAssignments,EffectiveBasePermissions')
        permissions = meta.get('EffectiveBasePermissions') or {}
        try:
            permitted = bool(int(permissions.get('Low', '0')) & MANAGE_PERMISSIONS)
        except (ValueError, TypeError):
            permitted = False
        if not permitted:
            raise SharePointProbeError('이 항목의 권한을 변경할 Microsoft 권한이 없습니다.')
        raw = self.call(self.resource + '/roleassignments?$expand=Member,RoleDefinitionBindings')
        entries = raw.get('value', raw.get('results'))
        if not isinstance(entries, list) or len(entries) > 100 or raw.get('@odata.nextLink') or raw.get('__next'):
            raise SharePointProbeError('권한 구성이 복잡하여 자동 변경할 수 없습니다. 저장소 관리에서 확인하세요.')
        rows = []
        for entry in entries:
            member = entry.get('Member') or {}
            roles = entry.get('RoleDefinitionBindings') or []
            if isinstance(roles, dict):
                roles = roles.get('results', [])
            if not isinstance(roles, list) or not all(isinstance(r, dict) for r in roles):
                raise SharePointProbeError('권한 정보를 확인하지 못했습니다.')
            try:
                pid = int(member['Id'])
                bindings = sorted((int(r['Id']), int(r['RoleTypeKind'])) for r in roles)
                principal_type = int(member['PrincipalType'])
            except (KeyError, ValueError, TypeError):
                raise SharePointProbeError('권한 대상 정보를 확인하지 못했습니다.') from None
            try:
                manages = any(int((r.get('BasePermissions') or {}).get('Low', 0)) & MANAGE_PERMISSIONS for r in roles)
            except (ValueError, TypeError):
                raise SharePointProbeError('사용자 정의 권한을 확인하지 못했습니다.') from None
            if any(kind == 0 for _, kind in bindings) and not all('BasePermissions' in r for r in roles):
                raise SharePointProbeError('사용자 정의 권한을 확인하지 못했습니다.')
            protected = pid == int(self.identity['Id']) or any(kind == 5 for _, kind in bindings) or manages
            rows.append({'id': pid, 'name': str(member.get('Title', ''))[:200],
                'email': str(member.get('Email', '')).casefold(), 'type': principal_type,
                'roles': bindings, 'protected': protected,
                'limited': all(kind == 1 for _, kind in bindings)})
        if not rows or not any(r['protected'] for r in rows):
            raise SharePointProbeError('관리자 접근을 보존할 수 없어 권한 변경을 중단했습니다.')
        return {'unique': bool(meta.get('HasUniqueRoleAssignments')), 'rows': sorted(rows, key=lambda r: r['id'])}

    def roles(self):
        raw = self.call('roledefinitions?$select=Id,RoleTypeKind')
        entries = raw.get('value', raw.get('results', []))
        try:
            if not isinstance(entries, list) or len(entries) > 100 or raw.get('@odata.nextLink') or raw.get('__next'):
                raise ValueError
            result = {int(r['RoleTypeKind']): int(r['Id']) for r in entries}
        except (ValueError, TypeError, KeyError):
            raise SharePointProbeError('기본 보기·편집 권한을 확인하지 못했습니다.') from None
        if 2 not in result or 3 not in result:
            raise SharePointProbeError('기본 보기·편집 권한을 확인하지 못했습니다.')
        return {'read': result[2], 'write': result[3]}

    def principal(self, email):
        result = self.call('ensureuser', 'POST', {'logonName': 'i:0#.f|membership|' + email})
        expected = {str(result.get('Email', '')).casefold(), str(result.get('LoginName', '')).rsplit('|', 1)[-1].casefold()}
        try:
            principal_type, principal_id = int(result.get('PrincipalType', 0)), int(result['Id'])
        except (ValueError, TypeError, KeyError):
            raise SharePointProbeError('해당 직원의 회사 Microsoft 계정을 확인하지 못했습니다.') from None
        if email.casefold() not in expected or principal_type != 1 or principal_id < 1:
            raise SharePointProbeError('해당 직원의 회사 Microsoft 계정을 확인하지 못했습니다.')
        return principal_id

    def apply(self, expected, desired, observe):
        """desired: [{email, role}]. Fail closed on concurrent/partial state, no auto retry."""
        current = self.snapshot()
        if digest(current) != digest(expected):
            raise SharePointProbeError('다른 곳에서 권한이 변경되었습니다. 현재 권한을 다시 확인하세요.')
        role_ids = self.roles()
        planned = [(self.principal(r['email']), role_ids[r['role']]) for r in desired]
        if len({pid for pid, _ in planned}) != len(planned):
            raise SharePointProbeError('같은 Microsoft 계정에 연결된 직원 이메일이 중복됩니다.')
        # ensureuser only resolves directory identities; recheck ACL before mutating it.
        if digest(self.snapshot()) != digest(current):
            raise SharePointProbeError('다른 곳에서 권한이 변경되었습니다. 현재 권한을 다시 확인하세요.')
        if not current['unique']:
            copied = current
            self.call(self.resource + '/breakroleinheritance(copyRoleAssignments=true,clearSubscopes=false)', 'POST')
            current = self.snapshot()
            observe('권한 상속 분리', current)
            if digest(current['rows']) != digest(copied['rows']):
                raise SharePointProbeError('권한 분리 중 기존 권한 구성이 달라졌습니다. 현재 권한을 다시 확인하세요.')
        protected = {r['id'] for r in current['rows'] if r['protected'] or r['limited']}
        # Add intended access before removing old assignments. Keep owner/caller access.
        for pid, role in planned:
            if pid in protected:
                continue
            row = next((r for r in current['rows'] if r['id'] == pid), None)
            if not row or role not in [rid for rid, _ in row['roles']]:
                current = self.step(current, f'/roleassignments/addroleassignment(principalid={pid},roledefid={role})', 'POST', observe, '직원 권한 추가')
        wanted = dict(planned)
        for row in list(current['rows']):
            if row['id'] in protected:
                continue
            for rid, _ in row['roles']:
                if wanted.get(row['id']) != rid:
                    current = self.step(current, f"/roleassignments/removeroleassignment(principalid={row['id']},roledefid={rid})", 'POST', observe, '기존 권한 해제')
        actual = {(r['id'], rid) for r in current['rows'] if r['id'] not in protected for rid, _ in r['roles']}
        if actual != {(pid, role) for pid, role in planned if pid not in protected}:
            raise SharePointProbeError('요청한 권한과 실제 결과가 다릅니다. 현재 권한을 확인하세요.')
        return current

    def step(self, expected, suffix, method, observe, label):
        if digest(self.snapshot()) != digest(expected):
            raise SharePointProbeError('처리 중 외부 권한 변경이 확인되어 중단했습니다. 현재 권한을 다시 확인하세요.')
        self.call(self.resource + suffix, method)
        actual = self.snapshot()
        observe(label, actual)
        return actual
