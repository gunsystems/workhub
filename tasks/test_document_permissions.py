from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import patch, Mock
from django.contrib.auth import get_user_model
from django.test import TestCase, Client
from django.utils import timezone
from datetime import timedelta
from .models import Department, Employee, SystemAudit, DocumentPermissionLease
from .document_permission_api import PermissionClient, digest
from .document_permissions import AccessForm
from .sharepoint_probe import SharePointProbeError


def row(pid, name, email='', kind=2, protected=False):
    return {'id': pid, 'name': name, 'email': email, 'type': 1, 'roles': [(100 + kind, kind)],
            'protected': protected, 'limited': kind == 1}


class MutablePermissions(PermissionClient):
    def __init__(self):
        self.value = {'unique': False, 'rows': [row(1, '소유자', kind=5, protected=True),
            row(2, '기존 그룹', kind=3), row(3, '직원', 'worker@gunsystems.co.kr')]}
        self.operations = []
        self.resource = 'safe-item'
        self.identities = {'worker@gunsystems.co.kr': 3, 'new@gunsystems.co.kr': 4}

    def snapshot(self):
        return deepcopy(self.value)

    def principal(self, email):
        return self.identities[email]

    def roles(self):
        return {'read': 102, 'write': 103}

    def call(self, suffix, method='GET', data=None):
        import re
        self.operations.append(suffix)
        if 'breakroleinheritance' in suffix:
            self.value['unique'] = True
        elif 'roleassignment(' in suffix:
            pid, role_id = map(int, re.search(r'principalid=(\d+),roledefid=(\d+)', suffix).groups())
            person = next((r for r in self.value['rows'] if r['id'] == pid), None)
            if 'addroleassignment' in suffix:
                if person is None:
                    person = row(pid, '새 직원', 'new@gunsystems.co.kr')
                    person['roles'] = []
                    self.value['rows'].append(person)
                person['roles'].append((role_id, role_id - 100))
                person['roles'].sort()
            else:
                person['roles'] = [r for r in person['roles'] if r[0] != role_id]
                if not person['roles']:
                    self.value['rows'].remove(person)
        self.value['rows'].sort(key=lambda r: r['id'])
        return {}


class PermissionApiTests(TestCase):
    def test_adds_new_access_before_removing_groups_preserves_owner_and_records_each_step(self):
        api = MutablePermissions()
        old = api.snapshot()
        observed = []
        result = api.apply(old, [{'email': 'new@gunsystems.co.kr', 'role': 'write'}], lambda *a: observed.append(a))
        self.assertTrue(result['unique'])
        self.assertEqual([r['id'] for r in result['rows']], [1, 4])
        self.assertIn('copyRoleAssignments=true,clearSubscopes=false', api.operations[0])
        self.assertIn('addroleassignment', api.operations[1])
        self.assertFalse(any('principalid=1,' in p for p in api.operations))
        self.assertEqual(len(observed), len(api.operations))

    def test_stale_acl_is_rejected_before_any_write(self):
        api = MutablePermissions()
        old = api.snapshot()
        api.value['rows'].append(row(8, '외부 변경'))
        with self.assertRaises(SharePointProbeError):
            api.apply(old, [], Mock())
        self.assertEqual(api.operations, [])

    def test_changed_assignments_during_inheritance_break_stops_before_removal(self):
        api = MutablePermissions()
        original = api.call
        def call(*args, **kwargs):
            result = original(*args, **kwargs)
            api.value['rows'].append(row(9, '동시 변경'))
            return result
        api.call = call
        with self.assertRaises(SharePointProbeError):
            api.apply(api.snapshot(), [], Mock())
        self.assertEqual(len(api.operations), 1)

    def test_empty_allowlist_keeps_owners(self):
        api = MutablePermissions()
        result = api.apply(api.snapshot(), [], Mock())
        self.assertEqual([r['id'] for r in result['rows']], [1])

    def test_failure_never_retries_or_continues_removal(self):
        api = MutablePermissions()
        api.value['unique'] = True
        api.call = Mock(side_effect=SharePointProbeError('중단'))
        with self.assertRaises(SharePointProbeError):
            api.apply(api.snapshot(), [{'email': 'new@gunsystems.co.kr', 'role': 'write'}], Mock())
        self.assertEqual(api.call.call_count, 1)

    def test_connection_is_not_available_to_employee_even_if_site_owner(self):
        user = get_user_model().objects.create_user('member')
        from django.core.exceptions import PermissionDenied
        with self.assertRaises(PermissionDenied):
            PermissionClient(user)

    def test_transport_refuses_redirect_and_does_not_expose_provider_body(self):
        api = object.__new__(PermissionClient)
        api.config = SimpleNamespace(site_url='https://tenant.sharepoint.com/sites/work')
        api.token = 'secret'
        response = Mock(status_code=302)
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        with patch('tasks.document_permission_api.requests.request', return_value=response) as send:
            with self.assertRaises(SharePointProbeError): api.call('currentuser')
        self.assertFalse(send.call_args.kwargs['allow_redirects'])
        self.assertEqual(send.call_args.args[1], 'https://tenant.sharepoint.com/sites/work/_api/web/currentuser')

    def test_missing_scope_connection_does_not_fallback_to_application_token(self):
        user = get_user_model().objects.create_superuser('owner', 'owner@gunsystems.co.kr', 'unused')
        with patch('tasks.document_permission_api.ProbeConfig.from_settings', return_value=SimpleNamespace(
                tenant_id='t', client_id='c', site_url='https://tenant.sharepoint.com/sites/work')), patch(
                'tasks.document_permission_api.token_request') as token:
            with self.assertRaises(SharePointProbeError): PermissionClient(user)
            token.assert_not_called()


class PermissionViewTests(TestCase):
    def setUp(self):
        users = get_user_model()
        self.admin = users.objects.create_superuser('admin', 'admin@gunsystems.co.kr', 'unused')
        self.member = users.objects.create_user('worker', email='worker@gunsystems.co.kr')
        dept = Department.objects.create(name='기술팀')
        self.staff = Employee.objects.create(user=self.member, name='직원', department=dept)
        self.url = '/work/shared-documents/drive/folder/permissions/'
        self.client.force_login(self.admin)
        self.factory = patch('tasks.document_permissions.PermissionClient').start()
        self.addCleanup(patch.stopall)
        self.api = self.factory.return_value
        self.api.config.site_url = 'https://tenant.sharepoint.com/sites/work'
        self.api.connection.connected_at = timezone.now()
        self.api.connection.email = 'owner@gunsystems.co.kr'
        self.api.target.return_value = {'name': '제안서', 'folder': {}}
        self.snapshot = MutablePermissions().snapshot()
        self.api.snapshot.return_value = self.snapshot
        self.api.apply.return_value = {**self.snapshot, 'unique': True}

    def preview(self):
        response = self.client.post(self.url, {'reason': '제안서 공동작업', 'employee_' + str(self.staff.pk): 'write'})
        self.assertEqual(response.status_code, 200)
        return response.context['nonce']

    def test_get_and_review_do_not_change_permissions(self):
        response = self.client.get(self.url)
        self.assertContains(response, '접근할 직원 선택')
        self.preview()
        self.api.apply.assert_not_called()
        self.assertFalse(SystemAudit.objects.filter(target_type='SharePointPermission').exists())

    def test_apply_requires_explicit_confirmation_and_is_one_use(self):
        nonce = self.preview()
        self.client.post(self.url + 'apply/', {'nonce': nonce})
        self.api.apply.assert_not_called()
        nonce = self.preview()
        self.client.post(self.url + 'apply/', {'nonce': nonce, 'confirmed': 'yes', 'email': 'attacker@outside.test'})
        self.assertEqual(self.api.apply.call_args.args[1][0]['email'], 'worker@gunsystems.co.kr')
        event = SystemAudit.objects.get(target_type='SharePointPermission', action='update')
        self.assertEqual(event.actor_name, 'admin')
        self.assertEqual(event.after['사유'], '제안서 공동작업')
        self.client.post(self.url + 'apply/', {'nonce': nonce, 'confirmed': 'yes'})
        self.assertEqual(self.api.apply.call_count, 1)

    def test_employee_cannot_view_preview_connect_or_apply(self):
        self.client.force_login(self.member)
        for method, url in [('get', self.url), ('post', self.url), ('post', self.url + 'apply/'),
                ('get', '/work/shared-documents/permissions/'), ('post', '/work/shared-documents/permissions/connect/')]:
            self.assertEqual(getattr(self.client, method)(url).status_code, 403)
        self.factory.assert_not_called()

    def test_staff_change_between_preview_and_apply_blocks_write(self):
        nonce = self.preview()
        self.staff.is_active = False
        self.staff.save()
        self.client.post(self.url + 'apply/', {'nonce': nonce, 'confirmed': 'yes'})
        self.api.apply.assert_not_called()

    def test_preview_bound_to_target_and_expiry(self):
        nonce = self.preview()
        self.client.post('/work/shared-documents/drive/another/permissions/apply/', {'nonce': nonce, 'confirmed': 'yes'})
        self.api.apply.assert_not_called()
        nonce = self.preview()
        session = self.client.session
        session['document_permission_preview'][nonce]['at'] -= 601
        session.save()
        self.client.post(self.url + 'apply/', {'nonce': nonce, 'confirmed': 'yes'})
        self.api.apply.assert_not_called()

    def test_busy_target_lease_blocks_overlapping_changes(self):
        key = digest([self.api.config.site_url, 'drive', 'folder'])
        DocumentPermissionLease.objects.create(target=key, expires_at=timezone.now()+timedelta(minutes=1))
        self.client.post(self.url+'apply/', {'nonce': self.preview(), 'confirmed': 'yes'})
        self.api.apply.assert_not_called()
        self.assertTrue(DocumentPermissionLease.objects.filter(target=key).exists())

    def test_partial_provider_failure_is_logged_without_success(self):
        self.api.apply.side_effect = SharePointProbeError('중간에 중단됐습니다.')
        self.client.post(self.url+'apply/', {'nonce': self.preview(), 'confirmed': 'yes'})
        self.assertTrue(SystemAudit.objects.filter(action='permission_attention').exists())
        self.assertFalse(SystemAudit.objects.filter(action='update', target_type='SharePointPermission').exists())
        self.assertFalse(DocumentPermissionLease.objects.exists())

    def test_csrf_is_required(self):
        strict = Client(enforce_csrf_checks=True)
        strict.force_login(self.admin)
        self.assertEqual(strict.post(self.url+'apply/', {}).status_code, 403)

    def test_oauth_state_mismatch_does_not_exchange_code(self):
        with patch('tasks.document_permissions.ProbeConfig.from_settings'), patch('tasks.document_permissions.token_request') as token:
            self.client.get('/work/shared-documents/permissions/callback/?state=wrong&code=secret')
            token.assert_not_called()

    def test_limited_access_is_not_preselected_as_read(self):
        snapshot = {'rows': [row(3, '직원', 'worker@gunsystems.co.kr', kind=1)]}
        self.staff.email = 'worker@gunsystems.co.kr'
        form = AccessForm(employees=[self.staff], snapshot=snapshot)
        self.assertEqual(form.fields['employee_' + str(self.staff.pk)].initial, 'none')

    def test_protected_staff_is_displayed_as_admin_and_cannot_be_demoted(self):
        self.staff.email = 'worker@gunsystems.co.kr'
        form = AccessForm({'reason':'사유', 'employee_' + str(self.staff.pk):'none'},
            employees=[self.staff], snapshot={'rows':[row(3, '직원', self.staff.email, kind=5, protected=True)]})
        self.assertTrue(form.is_valid())
        self.assertEqual(form.cleaned_data['employee_' + str(self.staff.pk)], 'owner')
        self.assertEqual(form.desired(), [])
