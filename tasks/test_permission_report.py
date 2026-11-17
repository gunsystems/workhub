from copy import deepcopy
from unittest.mock import Mock, patch
from django.contrib.auth import get_user_model
from django.test import TestCase
from .permission_report import grants
from .sharepoint_probe import SharePointProbeError


class PermissionReportTests(TestCase):
    def setUp(self):
        self.admin = get_user_model().objects.create_superuser('report-admin', 'admin@example.com', 'unused')
        self.client.force_login(self.admin)
        self.api = Mock()
        self.api.docs.libraries = [{'id':'drive', 'name':'전사 공통'}]
        self.api.bind.return_value = {'id':'folder', 'name':'회사 양식', 'folder':{}}
        self.state = {'unique':False, 'source':{'name':'문서 사이트'}, 'rows':[
            {'id':3, 'name':'관리자', 'email':'', 'type':8, 'roles':[(105,5)], 'protected':True, 'limited':False},
            {'id':19, 'name':'전 직원', 'email':'', 'type':8, 'roles':[(102,2)], 'protected':False, 'limited':False},
            {'id':7, 'name':'대표', 'email':'lee@example.com', 'type':1, 'roles':[(105,5)], 'protected':True, 'limited':False},
            {'id':12, 'name':'경로 접근', 'email':'', 'type':8, 'roles':[(101,1)], 'protected':False, 'limited':True}]}
        self.api.state.return_value = self.state
        self.api.docs.children_page.return_value = ([{'id':'child', 'name':'양식', 'folder':{}},
                                                     {'id':'file', 'name':'문서.docx', 'file':{}}], 'cursor', 2)
        self.api.groups.return_value = [{'id':19, 'name':'전 직원'}]
        self.api.members.return_value = [{'id':8, 'name':'직원', 'email':'staff@example.com', 'type':1}]
        self.base = '/work/shared-documents/permissions/report/'

    def test_all_endpoints_block_nonadmins_before_connecting(self):
        urls = [self.base, self.base+'node/?drive=drive', self.base+'groups/19/']
        for user in (None, get_user_model().objects.create_user('employee')):
            self.client.logout()
            if user: self.client.force_login(user)
            with patch('tasks.permission_report.GroupPermissionClient') as connect:
                for url in urls:
                    self.assertEqual(self.client.get(url).status_code, 403 if user else 302)
                connect.assert_not_called()

    def test_shell_and_api_are_private_get_only(self):
        with patch('tasks.permission_report.GroupPermissionClient', return_value=self.api):
            for url in (self.base, self.base+'node/?drive=drive', self.base+'groups/19/'):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 200)
                self.assertIn('no-store', response.headers['Cache-Control'])
                self.assertEqual(self.client.post(url).status_code, 405)
            self.assertContains(self.client.get(self.base), '개별 파일의 별도 공유')

    def test_node_fresh_acl_bounded_page_and_protected_person(self):
        with patch('tasks.permission_report.GroupPermissionClient', return_value=self.api) as connect:
            result = self.client.get(self.base+'node/?drive=drive&item=folder&cursor=cursor').json()
            self.client.get(self.base+'node/?drive=drive&item=folder')
        self.assertEqual(connect.call_count, 2)
        self.assertEqual(self.api.state.call_count, 2)
        self.assertEqual(result['children'], [{'id':'child','name':'양식'}])
        self.assertEqual(result['cursor'], 'cursor')
        self.assertEqual([r['id'] for r in result['grants']], [3,19,7])
        self.assertEqual(result['grants'][2]['kind'], 'person')
        self.assertFalse(result['unique'])
        self.api.docs.children_page.assert_any_call('drive','folder',cursor='cursor')
        self.assertNotIn('token', repr(result))

    def test_file_cannot_be_a_folder_report(self):
        self.api.bind.return_value = {'file':{}}
        with patch('tasks.permission_report.GroupPermissionClient', return_value=self.api):
            self.assertEqual(self.client.get(self.base+'node/?drive=drive&item=file').status_code,404)
        self.api.state.assert_not_called()

    def test_revoked_access_and_incomplete_page_are_errors_not_empty_grants(self):
        for method in (self.api.bind, self.api.state, self.api.docs.children_page):
            method.side_effect = SharePointProbeError('private provider details')
            with patch('tasks.permission_report.GroupPermissionClient', return_value=self.api):
                response = self.client.get(self.base+'node/?drive=drive&item=folder')
            self.assertEqual(response.status_code,400)
            self.assertNotIn('private provider',response.content.decode())
            self.assertNotIn('grants',response.json())
            method.side_effect = None

    def test_member_lookup_restricts_group_to_selected_site_catalog(self):
        with patch('tasks.permission_report.GroupPermissionClient', return_value=self.api):
            response = self.client.get(self.base+'groups/999/')
        self.assertEqual(response.status_code,404)
        self.api.members.assert_not_called()

    def test_empty_members_and_failed_members_are_distinct(self):
        self.api.members.return_value = []
        with patch('tasks.permission_report.GroupPermissionClient', return_value=self.api):
            self.assertEqual(self.client.get(self.base+'groups/19/').json()['members'], [])
            self.api.members.side_effect = SharePointProbeError('no access')
            result = self.client.get(self.base+'groups/19/')
            self.assertEqual(result.status_code,400)
            self.assertNotIn('members', result.json())

    def test_custom_rights_are_not_reported_as_standard_edit(self):
        row = deepcopy(self.state['rows'][0]); row['roles'] = [(999,0)]
        self.assertEqual(grants({'rows':[row]})[0]['role'], '사용자 정의 (별도 확인)')
