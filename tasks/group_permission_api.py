"""Group-oriented administration; SharePoint is the authorization source.

No Graph invite/inheritedFrom and no tenant-wide group permissions. All writes
are explicit deltas, with fresh state checks and no automatic inheritance break.
"""
import uuid
from urllib.parse import urlencode
from .document_permission_api import PermissionClient, digest
from .shared_documents import GRAPH, segment
from .sharepoint_probe import _json_request, SharePointProbeError


def entries(raw):
    values = raw.get('value', raw.get('results'))
    if (not isinstance(values, list) or len(values) > 500 or
            raw.get('@odata.nextLink') or raw.get('odata.nextLink') or raw.get('__next')):
        raise SharePointProbeError('목록을 모두 확인하지 못했습니다. 범위를 줄여 다시 조회하세요.')
    return values


class GroupPermissionClient(PermissionClient):
    def call(self, suffix, method='GET', data=None):
        return super().call(suffix.lstrip('/'), method, data)

    def roles(self):
        result = super().roles()
        roles = entries(self.call('roledefinitions?$select=Id,RoleTypeKind'))
        manage = next((int(r['Id']) for r in roles if int(r['RoleTypeKind']) == 5), None)
        if not manage:
            raise SharePointProbeError('관리 권한을 확인하지 못했습니다.')
        return {**result, 'manage': manage}

    def bind(self, drive, item='root'):
        self.docs.library(drive)
        if item == 'root':
            endpoint = f'{GRAPH}/drives/{segment(drive)}/root'
            row = _json_request(endpoint + '?$select=id,name,folder,root,parentReference', token=self.docs.token)
        else:
            row = self.docs.item(drive, item)
            endpoint = f'{GRAPH}/drives/{segment(drive)}/items/{segment(item)}'
        raw = _json_request(endpoint + '?$select=sharepointIds', token=self.docs.token)
        try:
            self.list_id = str(uuid.UUID(raw['sharepointIds']['listId']))
            base = f"lists(guid'{self.list_id}')"
            if item == 'root' or 'root' in row:
                self.resource = base
            else:
                number = int(raw['sharepointIds']['listItemId'])
                if number < 1:
                    raise ValueError
                self.resource = base + f'/items({number})'
        except (KeyError, ValueError, TypeError):
            raise SharePointProbeError('문서 권한 위치를 확인하지 못했습니다.') from None
        self.drive, self.item_id, self.target_row = drive, item, row
        return row

    def state(self):
        """Resolve nearest unique parent, including library and web boundaries.

        Walking Graph parents only locates the objects. HasUniqueRoleAssignments
        and RoleAssignments are always read from SharePoint REST.
        """
        original = self.resource
        source = {'resource': original, 'name': self.target_row['name'],
                  'drive': self.drive, 'item': self.item_id}
        meta = self.call(original + '?$select=HasUniqueRoleAssignments')
        unique = meta.get('HasUniqueRoleAssignments')
        if type(unique) is not bool:
            raise SharePointProbeError('권한 상속 상태를 확인하지 못했습니다.')
        visited = {self.item_id}
        row = self.target_row
        for _ in range(64):
            if meta.get('HasUniqueRoleAssignments') is True:
                break
            if source['resource'] == f"lists(guid'{self.list_id}')":
                # Only the configured web is in scope. Never walk into parent sites.
                web = self.call('?$select=HasUniqueRoleAssignments,Title')
                if web.get('HasUniqueRoleAssignments') is not True:
                    raise SharePointProbeError('사이트 밖의 상속 원본은 이 화면에서 변경하지 않습니다.')
                source = {'resource': '', 'name': web.get('Title', '문서 사이트'), 'drive': '', 'item': ''}
                meta = web
                break
            parent = (row.get('parentReference') or {}).get('id')
            if not parent or parent in visited:
                raise SharePointProbeError('상위 폴더를 확인하지 못했습니다.')
            visited.add(parent)
            row = self.docs.item(self.drive, parent)
            if 'root' in row:
                resource = f"lists(guid'{self.list_id}')"
                parent = 'root'
            else:
                ids = _json_request(f'{GRAPH}/drives/{segment(self.drive)}/items/{segment(parent)}'
                    '?$select=sharepointIds', token=self.docs.token).get('sharepointIds', {})
                if str(ids.get('listId', '')).lower() != self.list_id:
                    raise SharePointProbeError('상속 원본이 문서함 범위를 벗어났습니다.')
                try:
                    number = int(ids['listItemId'])
                    if number < 1:
                        raise ValueError
                except (KeyError, ValueError, TypeError):
                    raise SharePointProbeError('상속 원본을 확인하지 못했습니다.') from None
                resource = f"lists(guid'{self.list_id}')/items({number})"
            meta = self.call(resource + '?$select=HasUniqueRoleAssignments')
            if type(meta.get('HasUniqueRoleAssignments')) is not bool:
                raise SharePointProbeError('상속 원본을 확인하지 못했습니다.')
            source = {'resource': resource, 'name': row['name'], 'drive': self.drive, 'item': parent}
        else:
            raise SharePointProbeError('폴더 단계가 많아 상속 원본 조회를 중단했습니다.')
        try:
            self.resource = source['resource']
            # snapshot requires ManagePermissions and preserves owner/actor rows.
            value = self.snapshot()
        finally:
            self.resource = original
        value.update(unique=unique, source=source)
        return value

    def groups(self):
        values = entries(self.call('sitegroups?$select=Id,Title,LoginName,PrincipalType&$top=500'))
        return sorted([{'id': int(r['Id']), 'name': r['Title'],
                        'login': r.get('LoginName', '')} for r in values], key=lambda r: r['name'])

    def parent_state(self):
        saved = {name: getattr(self, name) for name in ('resource', 'list_id', 'drive', 'item_id', 'target_row')}
        try:
            if self.resource == f"lists(guid'{self.list_id}')":
                meta = self.call('?$select=Title,HasUniqueRoleAssignments')
                if meta.get('HasUniqueRoleAssignments') is not True:
                    raise SharePointProbeError('사이트 밖의 상속 원본은 이 화면에서 변경하지 않습니다.')
                self.resource = ''
                value = self.snapshot()
                value['source'] = {'resource':'', 'name':meta.get('Title','문서 사이트'), 'drive':'', 'item':''}
                return value
            parent = (self.target_row.get('parentReference') or {}).get('id')
            if not parent or parent == self.item_id:
                raise SharePointProbeError('복원할 상위 권한을 확인하지 못했습니다.')
            self.bind(self.drive, parent)
            return self.state()
        finally:
            for name, value in saved.items():
                setattr(self, name, value)

    def members(self, group_id):
        group_id = int(group_id)
        if group_id < 1:
            raise SharePointProbeError('그룹을 확인해 주세요.')
        values = entries(self.call(f'sitegroups/getbyid({group_id})/users'
            '?$select=Id,Title,Email,LoginName,PrincipalType,IsSiteAdmin&$top=500'))
        return sorted([{'id': int(r['Id']), 'name': r.get('Title', ''),
            'email': r.get('Email', '').casefold(), 'login': r.get('LoginName', ''),
            'type': int(r.get('PrincipalType', 0)), 'site_admin': bool(r.get('IsSiteAdmin'))}
            for r in values], key=lambda r: r['id'])

    def employee_groups(self, email):
        # One lookup, not one member-list request per group. No ensureuser on GET.
        query = urlencode({'$filter': "Email eq '" + email.replace("'", "''") + "'",
                           '$select': 'Id,Email,LoginName', '$top': 2})
        users = entries(self.call('siteusers?' + query))
        if len(users) != 1 or users[0].get('Email', '').casefold() != email.casefold():
            raise SharePointProbeError('직원의 Microsoft 사용자 정보를 확인하지 못했습니다. 본인 문서 계정 연결을 먼저 확인하세요.')
        pid = int(users[0]['Id'])
        groups = entries(self.call(f'getuserbyid({pid})/groups?$select=Id,Title&$top=500'))
        return [{'id': int(g['Id']), 'name': g['Title']} for g in groups]

    def create_group(self, name):
        # The site owner stays the owner of new groups. Members cannot edit membership.
        if not name.startswith('Workhub_') or len(name) > 100:
            raise SharePointProbeError('그룹 이름을 확인해 주세요.')
        result = self.call('sitegroups', 'POST', {'Title': name,
            'Description': 'Workhub 문서 접근 그룹', 'OnlyAllowMembersViewMembership': False,
            'AllowMembersEditMembership': False})
        try:
            gid = int(result['Id'])
            if gid < 1 or result['Title'] != name:
                raise ValueError
        except (KeyError, ValueError, TypeError):
            raise SharePointProbeError('그룹 생성 결과를 다시 확인해 주세요.') from None
        return gid

    def group_change(self, group_id, before, additions, removals, observe):
        """Only explicit member deltas. Do not replace hidden/nested membership."""
        if digest(self.members(group_id)) != digest(before):
            raise SharePointProbeError('그룹 구성원이 변경되었습니다. 다시 검토하세요.')
        protected = {r['id'] for r in before if r['site_admin'] or r['id'] == int(self.identity['Id'])}
        existing = {r['id'] for r in before}
        if set(removals) - existing or set(removals) & protected:
            raise SharePointProbeError('관리자 구성원은 이 화면에서 제외할 수 없습니다.')
        planned = [self.principal(email) for email in additions]
        if len(set(planned)) != len(planned) or set(planned) & set(removals):
            raise SharePointProbeError('구성원 변경 대상이 중복됩니다.')
        current = before
        for pid in planned:
            if pid in existing:
                continue
            if digest(self.members(group_id)) != digest(current):
                raise SharePointProbeError('처리 중 구성원이 변경되었습니다. 다시 조회하세요.')
            identity = self.call(f'getuserbyid({pid})?$select=LoginName')
            self.call(f'sitegroups/getbyid({int(group_id)})/users', 'POST', {'LoginName': identity['LoginName']})
            after = self.members(group_id)
            if {r['id'] for r in after} != {r['id'] for r in current} | {pid}:
                raise SharePointProbeError('구성원 추가 결과를 확인해 주세요.')
            observe('그룹 구성원 추가', after)
            current = after
        for pid in removals:
            if digest(self.members(group_id)) != digest(current):
                raise SharePointProbeError('처리 중 구성원이 변경되었습니다. 다시 조회하세요.')
            self.call(f'sitegroups/getbyid({int(group_id)})/users/removebyid({int(pid)})', 'POST')
            after = self.members(group_id)
            if {r['id'] for r in after} != {r['id'] for r in current} - {pid}:
                raise SharePointProbeError('구성원 제외 결과를 확인해 주세요.')
            observe('그룹 구성원 제외', after)
            current = after
        return current

    def change(self, before, action, changes, observe, *, parent=None):
        if digest(self.state()) != digest(before):
            raise SharePointProbeError('권한이 변경되었습니다. 다시 검토하세요.')
        if action == 'break':
            if before['unique']:
                raise SharePointProbeError('이미 별도 권한을 사용하고 있습니다.')
            self.call(self.resource + '/breakroleinheritance(copyRoleAssignments=true,clearSubscopes=false)', 'POST')
            after = self.state()
            observe('상위 권한을 복사하여 별도 설정', after)
            if not after['unique'] or digest(after['rows']) != digest(before['rows']):
                raise SharePointProbeError('상속 분리 결과를 확인해 주세요.')
            return after
        if action == 'reset':
            if not before['unique']:
                raise SharePointProbeError('이미 상위 권한을 사용하고 있습니다.')
            current_parent = self.parent_state()
            if parent is None or digest(parent) != digest(current_parent):
                raise SharePointProbeError('복원할 상위 권한이 변경되었습니다. 다시 검토하세요.')
            retained = {r['id'] for r in current_parent['rows'] if r['protected']}
            required = {r['id'] for r in before['rows'] if r['protected']}
            if self.identity.get('IsSiteAdmin'):
                required.discard(int(self.identity['Id']))
            if required - retained:
                raise SharePointProbeError('상위 권한에 기존 관리자가 포함되어 있지 않아 복원을 중단했습니다.')
            self.call(self.resource + '/resetroleinheritance()', 'POST')
            after = self.state()
            observe('상위 권한 상속 복원', after)
            if after['unique']:
                raise SharePointProbeError('상속 복원 결과를 확인해 주세요.')
            return after
        if action != 'groups' or not before['unique']:
            raise SharePointProbeError('상속 중에는 권한을 직접 변경할 수 없습니다. 상속 원본에서 변경하세요.')
        role_ids = self.roles()
        catalog = {r['id'] for r in self.groups()}
        protected = {r['id'] for r in before['rows'] if r['protected'] or r['limited']}
        expected = before
        for change in changes:
            pid, role = change['id'], change['role']
            if pid not in catalog or pid in protected or role not in ('none', 'read', 'write', 'manage'):
                raise SharePointProbeError('변경할 그룹 권한을 확인해 주세요.')
            row = next((r for r in before['rows'] if r['id'] == pid), None)
            if row and (row['type'] != 8 or any(kind not in (2, 3, 6) for _, kind in row['roles'])):
                raise SharePointProbeError('사용자 정의·관리 권한은 보존합니다.')
            wanted = role_ids.get(role)
            existing = [rid for rid, _ in row['roles']] if row else []
            if wanted and wanted not in existing:
                expected = self.mutate(expected, f'/roleassignments/addroleassignment(principalid={pid},roledefid={wanted})', observe)
            for rid in existing:
                if rid != wanted:
                    expected = self.mutate(expected, f'/roleassignments/removeroleassignment(principalid={pid},roledefid={rid})', observe)
        return expected

    def mutate(self, before, suffix, observe):
        if digest(self.state()) != digest(before):
            raise SharePointProbeError('처리 중 외부 변경이 확인되어 중단했습니다.')
        self.call(self.resource + suffix, 'POST')
        after = self.state()
        observe('폴더 그룹 권한 변경', after)
        import re
        pid, rid = map(int, re.search(r'principalid=(\d+),roledefid=(\d+)', suffix).groups())
        wanted = {(r['id'], role_id) for r in before['rows'] for role_id, _ in r['roles']}
        if '/roleassignments/addroleassignment' in suffix:
            wanted.add((pid, rid))
        else:
            wanted.discard((pid, rid))
        actual = {(r['id'], role_id) for r in after['rows'] for role_id, _ in r['roles']}
        if wanted != actual or not after['unique']:
            raise SharePointProbeError('요청과 실제 권한이 다릅니다. 다시 조회해 주세요.')
        return after
