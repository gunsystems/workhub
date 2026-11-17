from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.template.loader import get_template
from django.test import TestCase, override_settings
from django.http import Http404
from .shared_documents import UserDocuments, cipher, safe_web_url, segment, required_scope
from .sharepoint_probe import ProbeConfig, SharePointProbeError
from .models import DocumentConnection
from .models import Department, Employee, Task, Project, SharedDocument, SharedDocumentLink
from django.utils import timezone

SITE = 'https://example.sharepoint.com/sites/WorkhubDocuments'
TENANT = '11111111-1111-1111-1111-111111111111'
CLIENT = '22222222-2222-2222-2222-222222222222'


@override_settings(SHAREPOINT_TENANT_ID=TENANT, SHAREPOINT_CLIENT_ID=CLIENT,
                   SHAREPOINT_CLIENT_SECRET='test-secret', SHAREPOINT_SITE_URL=SITE)
class SharedDocumentsTests(TestCase):
    def setUp(self):
        from . import document_token_cache
        document_token_cache.clear()
        self.addCleanup(document_token_cache.clear)
        self.user = get_user_model().objects.create_user('document-user', email='staff@example.com')
        self.client.force_login(self.user)
        self.config = ProbeConfig.from_settings()
        self.library = {'id': 'drive-id', 'name': '공통', 'webUrl': SITE + '/DocLib'}
        self.file = {'id': 'file-id', 'name': 'proposal.docx', 'webUrl': SITE + '/DocLib/proposal.docx',
                     'file': {}, 'parentReference': {'driveId': 'drive-id'}, 'size': 10}

    def test_token_reuse_still_rechecks_site_and_rotation_invalidates(self):
        connection = self.connection()
        auth = {'scope': 'User.Read Sites.Selected', 'access_token': 'delegated',
                'refresh_token': 'rotated', 'expires_in': 3600}
        with patch('tasks.shared_documents.token_request', return_value=auth) as token, \
                patch('tasks.shared_documents.site_libraries', return_value=[self.library]) as libraries:
            UserDocuments(self.user)
            UserDocuments(self.user)
            self.assertEqual(token.call_count, 1)
            self.assertEqual(libraries.call_count, 2)
            connection.refresh_from_db()
            connection.encrypted_refresh_token = cipher().encrypt(b'reconnected').decode()
            connection.save()
            UserDocuments(self.user)
            self.assertEqual(token.call_count, 2)

    def test_cached_token_does_not_bypass_revoked_site(self):
        self.connection()
        with patch('tasks.shared_documents.token_request', return_value={
                'scope': 'User.Read Sites.Selected', 'access_token': 'delegated', 'expires_in': 3600}) as token, \
                patch('tasks.shared_documents.site_libraries', return_value=[self.library]) as libraries:
            UserDocuments(self.user)
            libraries.side_effect = SharePointProbeError('denied')
            with self.assertRaises(SharePointProbeError):
                UserDocuments(self.user)
            self.assertEqual(token.call_count, 1)

    def test_item_memo_is_only_within_one_request(self):
        client = object.__new__(UserDocuments)
        client.libraries, client.config, client.token, client._items = [self.library], self.config, 'token', {}
        with patch('tasks.shared_documents._json_request', return_value=self.file) as graph:
            client.item('drive-id', 'file-id')
            client.item('drive-id', 'file-id')
            self.assertEqual(graph.call_count, 1)
            client._items = {}
            client.item('drive-id', 'file-id')
            self.assertEqual(graph.call_count, 2)

    def test_token_cache_expiry_and_capacity(self):
        from . import document_token_cache as cache
        with patch('tasks.document_token_cache.monotonic', return_value=100):
            cache.put('one', b'encrypted', 5000)
            self.assertEqual(cache.get('one'), b'encrypted')
        with patch('tasks.document_token_cache.monotonic', return_value=401):
            self.assertIsNone(cache.get('one'))
            for i in range(130):
                cache.put(str(i), b'encrypted', 10)
            self.assertIsNone(cache.get('0'))
            self.assertEqual(len(cache._entries), 128)

    def connection(self, **values):
        data = dict(user=self.user, tenant=TENANT, client=CLIENT, subject=TENANT,
                    email=self.user.email, encrypted_refresh_token=cipher().encrypt(b'refresh').decode())
        data.update(values)
        return DocumentConnection.objects.create(**data)

    def test_permission_connection_cannot_be_borrowed_by_another_user(self):
        from .models import DocumentPermissionConnection
        from django.core.exceptions import PermissionDenied
        admin = get_user_model().objects.create_superuser('separate-admin', 'admin@gunsystems.co.kr', 'unused')
        connection = DocumentPermissionConnection.objects.create(user=admin, tenant=TENANT, client=CLIENT,
            subject=TENANT, email='owner@gunsystems.co.kr', site_url=SITE,
            encrypted_refresh_token=cipher().encrypt(b'refresh').decode())
        with self.assertRaises(PermissionDenied):
            UserDocuments(self.user, permission_connection=connection)
        other = get_user_model().objects.create_superuser('other-admin', 'other@gunsystems.co.kr', 'unused')
        with self.assertRaises(PermissionDenied):
            UserDocuments(other, permission_connection=connection)

    def test_admin_alias_is_site_bound_and_used_only_for_its_own_admin(self):
        from .models import DocumentPermissionConnection
        from django.core.exceptions import PermissionDenied
        admin = get_user_model().objects.create_superuser('separate-admin', 'admin@gunsystems.co.kr', 'unused')
        connection = DocumentPermissionConnection.objects.create(user=admin, tenant=TENANT, client=CLIENT,
            subject=TENANT, email='owner@gunsystems.co.kr', site_url=SITE,
            encrypted_refresh_token=cipher().encrypt(b'admin-refresh').decode())
        with patch('tasks.shared_documents.token_request', return_value={
                'scope':'User.Read Sites.Selected','access_token':'delegated'}) as token, \
                patch('tasks.shared_documents.site_libraries', return_value=[self.library]):
            UserDocuments(admin)
            self.assertEqual(token.call_args.args[1]['refresh_token'], 'admin-refresh')
            connection.site_url = 'https://example.sharepoint.com/sites/Other'
            connection.save()
            with self.assertRaises(PermissionDenied):
                UserDocuments(admin)

    def test_no_connection_never_uses_application_token(self):
        with patch('tasks.shared_documents.token_request') as token:
            with self.assertRaises(SharePointProbeError):
                UserDocuments(self.user)
            token.assert_not_called()

    def test_first_use_guidance_matches_connection_state_without_admin_setup(self):
        with patch('tasks.shared_documents.UserDocuments', side_effect=SharePointProbeError('연결이 필요합니다.')):
            response = self.client.get('/work/shared-documents/')
        self.assertContains(response, 'Workhub 로그인 이메일과 같은 회사 Microsoft 계정')
        self.assertContains(response, '함께 편집할 수 있습니다')
        self.assertContains(response, '권한이 자동으로 추가되지는 않습니다')
        self.assertNotContains(response, '관리자 · 문서 연결 설정')
        self.assertNotContains(response, 'Teams 연결과는 별도로')
        self.connection()
        with patch('tasks.shared_documents.UserDocuments') as client:
            client.return_value.libraries = []
            response = self.client.get('/work/shared-documents/')
        self.assertContains(response, '회사 계정 연결 완료')
        self.assertContains(response, '문서함이나 폴더 이름을 눌러 시작하세요')
        self.assertNotContains(response, '개인 Microsoft 계정 대신')

    def test_refresh_is_delegated_and_rotated_encrypted(self):
        connection = self.connection()
        with patch('tasks.shared_documents.token_request', return_value={
                'scope': 'User.Read Sites.Selected', 'access_token': 'delegated', 'refresh_token': 'rotated'}) as token, \
                patch('tasks.shared_documents.site_libraries', return_value=[self.library]) as libraries:
            client = UserDocuments(self.user)
        self.assertEqual(token.call_args.args[1]['grant_type'], 'refresh_token')
        libraries.assert_called_once_with(self.config, 'delegated', reuse_site_location=True)
        connection.refresh_from_db()
        self.assertNotEqual(connection.encrypted_refresh_token, 'rotated')
        self.assertEqual(cipher().decrypt(connection.encrypted_refresh_token.encode()), b'rotated')
        self.assertEqual(client.token, 'delegated')

    def test_wrong_identity_or_app_requires_reconnect(self):
        for values in ({'email': 'other@example.com'}, {'client': TENANT}, {'tenant': CLIENT}):
            self.connection(**values)
            with patch('tasks.shared_documents.token_request') as token:
                with self.assertRaises(SharePointProbeError): UserDocuments(self.user)
                token.assert_not_called()
            DocumentConnection.objects.all().delete()

    def test_scope_is_not_satisfied_by_teams_permission(self):
        for scope in ('User.Read OnlineMeetings.ReadWrite', 'Sites.Read.All User.Read', 'Sites.Selected'):
            with self.subTest(scope=scope), self.assertRaises(SharePointProbeError):
                required_scope({'scope': scope, 'access_token': 'token'})

    def test_url_and_identifier_boundaries(self):
        self.assertTrue(safe_web_url(self.file['webUrl'], self.config))
        for url in ('https://evil.test/a', SITE + 'Other/a', SITE + '/%2e%2e/private',
                    SITE + '/%252e%252e/private', SITE + '/a\nheader', SITE + '/a\\b'):
            self.assertFalse(safe_web_url(url, self.config), url)
        for value in ('..', '../users', 'abc?token=x', 'a/b'):
            with self.assertRaises(Http404): segment(value)

    def test_cross_drive_is_rejected_before_graph(self):
        client = object.__new__(UserDocuments)
        client.libraries = [self.library]
        with patch('tasks.shared_documents._json_request') as graph:
            with self.assertRaises(Http404): client.item('other-drive', 'file')
            graph.assert_not_called()

    def test_pagination_cannot_leave_resource(self):
        client = object.__new__(UserDocuments)
        client.libraries, client.config, client.token = [self.library], self.config, 'user-token'
        with patch('tasks.shared_documents._json_request', return_value={
                'value': [self.file], '@odata.nextLink': 'https://evil.test/steal'}) as graph:
            with self.assertRaises(SharePointProbeError): client.children('drive-id')
            self.assertEqual(graph.call_count, 1)

    def test_no_oauth_get_mutations(self):
        self.assertEqual(self.client.get('/work/shared-documents/connect/').status_code, 405)
        self.assertEqual(self.client.get('/work/shared-documents/disconnect/').status_code, 405)

    def page_client(self):
        client = object.__new__(UserDocuments)
        client.libraries, client.config, client.token = [self.library], self.config, 'user-token'
        client.cursor_identity, client._items = (1, 2, 'staff@example.com'), {}
        return client

    def batch_response(self, folder=None, children=None):
        return {'responses': [
            {'id': 'children', 'status': 200, 'body': {'value': [self.file]} if children is None else children},
            {'id': 'folder', 'status': 200, 'body': folder or self.folder_row()},
        ]}

    def test_batch_folder_and_children_share_one_roundtrip_and_fresh_ancestry(self):
        client = self.page_client()
        folder = self.folder_row(parentReference={'driveId': 'drive-id', 'id': 'root', 'path': '/drive/root:'})
        with patch('tasks.shared_documents._json_request', return_value=self.batch_response(folder)) as graph:
            rows, cursor, scanned = client.children_page('drive-id', 'folder-id', batch_folder=True)
            self.assertEqual((rows, cursor, scanned), ([self.file], '', 1))
            self.assertEqual(client.ancestry('drive-id', 'folder-id'), [{'id': 'folder-id', 'name': folder['name']}])
            self.assertEqual(graph.call_count, 1)
            self.assertTrue(graph.call_args.args[0].endswith('/$batch'))
            self.assertEqual(graph.call_args.kwargs['token'], 'user-token')
            requests = graph.call_args.kwargs['json_data']['requests']
            self.assertEqual([row['method'] for row in requests], ['GET', 'GET'])
            self.assertTrue(all(row['url'].startswith('/drives/drive-id/items/folder-id') for row in requests))
            client.children_page('drive-id', 'folder-id', batch_folder=True)
            self.assertEqual(graph.call_count, 2, 'later browse must revalidate, even on the same client')

    def test_batch_cursor_continues_only_the_same_folder_and_validates_nextlink(self):
        client = self.page_client()
        resource = 'https://graph.microsoft.com/v1.0/drives/drive-id/items/folder-id/children'
        response = self.batch_response(children={'value': [self.file], '@odata.nextLink': resource + '?$skiptoken=second'})
        with patch('tasks.shared_documents._json_request', return_value=response) as graph:
            _, cursor, _ = client.children_page('drive-id', 'folder-id', batch_folder=True)
            graph.return_value = self.batch_response()
            client.children_page('drive-id', 'folder-id', cursor=cursor, batch_folder=True)
            self.assertEqual(graph.call_args.kwargs['json_data']['requests'][1]['url'],
                '/drives/drive-id/items/folder-id/children?$skiptoken=second')
        response['responses'][0]['body']['@odata.nextLink'] = 'https://evil.test/steal'
        with patch('tasks.shared_documents._json_request', return_value=response):
            with self.assertRaises(SharePointProbeError):
                client.children_page('drive-id', 'folder-id', batch_folder=True)

    def test_ancestor_denial_does_not_render_already_fetched_rows(self):
        self.client.force_login(self.user)
        with patch('tasks.shared_documents.UserDocuments') as factory:
            factory.return_value.children_page.return_value = ([self.file], '', 1)
            factory.return_value.ancestry.side_effect = SharePointProbeError('denied')
            response = self.client.get('/work/shared-documents/?drive=drive-id&folder=folder-id', HTTP_X_WORKHUB_DOCUMENTS='1')
            self.assertNotIn('rows', response.context)
            self.assertContains(response, 'denied')
            self.assertIn('Server-Timing', response)
            self.assertIn('no-store', response['Cache-Control'])

    def test_batch_denial_or_malformed_subresponse_never_returns_rows(self):
        for status in (401, 403, 404, 429, 500, '200', [], True):
            for index in (0, 1):
                with self.subTest(status=status, index=index):
                    response = self.batch_response()
                    response['responses'][index]['status'] = status
                    client = self.page_client()
                    with patch('tasks.shared_documents._json_request', return_value=response) as graph:
                        with self.assertRaises(SharePointProbeError):
                            client.children_page('drive-id', 'folder-id', batch_folder=True)
                        self.assertEqual(graph.call_count, 1, 'no fallback that repeats denied requests')
                        self.assertEqual(client._items, {})
        for response in ({}, {'responses': []}, {'responses': [None, {}]},
                         {'responses': [{'id': 'folder'}, {'id': 'folder'}]}):
            with patch('tasks.shared_documents._json_request', return_value=response):
                with self.assertRaises(SharePointProbeError):
                    self.page_client().children_page('drive-id', 'folder-id', batch_folder=True)

    def test_batch_preserves_site_drive_item_and_cursor_boundaries(self):
        for folder in (self.folder_row(id='other'),
                       self.folder_row(webUrl='https://elsewhere.sharepoint.com/sites/private/folder'),
                       self.folder_row(parentReference={'driveId': 'other'})):
            with patch('tasks.shared_documents._json_request', return_value=self.batch_response(folder)):
                with self.assertRaises(SharePointProbeError):
                    self.page_client().children_page('drive-id', 'folder-id', batch_folder=True)
        with patch('tasks.shared_documents._json_request') as graph:
            with self.assertRaises(Http404):
                self.page_client().children_page('other-drive', 'folder-id', batch_folder=True)
            with self.assertRaises(SharePointProbeError):
                self.page_client().children_page('drive-id', 'folder-id', cursor='tampered', batch_folder=True)
            graph.assert_not_called()
        response = self.batch_response(children={'value': [dict(self.file, parentReference={'driveId': 'other'})]})
        with patch('tasks.shared_documents._json_request', return_value=response):
            with self.assertRaises(SharePointProbeError):
                self.page_client().children_page('drive-id', 'folder-id', batch_folder=True)

    def test_first_page_does_not_drain_large_folder_and_cursor_continues(self):
        client = self.page_client()
        resource = 'https://graph.microsoft.com/v1.0/drives/drive-id/root/children'
        with patch('tasks.shared_documents._json_request', return_value={
                'value': [self.file] * 50, '@odata.nextLink': resource + '?$skiptoken=page2'}) as graph:
            rows, cursor, scanned = client.children_page('drive-id')
            self.assertEqual((len(rows), scanned), (50, 50))
            self.assertTrue(cursor)
            self.assertNotIn('skiptoken', cursor)
            self.assertEqual(graph.call_count, 1)
            self.assertIn('%24top=50', graph.call_args.args[0])
            graph.return_value = {'value': [self.file]}
            rows, cursor, scanned = client.children_page('drive-id', cursor=cursor)
            self.assertEqual(graph.call_args.args[0], resource + '?$skiptoken=page2')
            self.assertEqual((scanned, cursor), (51, ''))

    def test_cursor_cannot_cross_user_drive_search_or_sort(self):
        client = self.page_client()
        next_url = 'https://graph.microsoft.com/v1.0/drives/drive-id/root/children?$skiptoken=page2'
        with patch('tasks.shared_documents._json_request', return_value={'value': [], '@odata.nextLink': next_url}):
            _, cursor, _ = client.children_page('drive-id')
        with patch('tasks.shared_documents._json_request') as graph:
            for kwargs in ({'query': 'changed'}, {'order': 'modified'}, {'cursor': cursor[:20] + 'x' + cursor[21:]}):
                with self.assertRaises(SharePointProbeError):
                    client.children_page('drive-id', **({'cursor': cursor} | kwargs))
            client.cursor_identity = (2, 2, 'other@example.com')
            with self.assertRaises(SharePointProbeError):
                client.children_page('drive-id', cursor=cursor)
            graph.assert_not_called()

    def test_cursor_expiry_and_external_nextlink_fail_closed(self):
        import json, time
        client = self.page_client()
        payload = {'scope': [list(client.cursor_identity), 'drive-id', '', '', 'name'],
                   'url': 'https://evil.test/steal', 'scanned': 0}
        with patch('tasks.shared_documents._json_request') as graph:
            for cursor in (cipher().encrypt(json.dumps(payload).encode()).decode(),
                           cipher().encrypt_at_time(json.dumps(payload).encode(), int(time.time()) - 901).decode()):
                with self.assertRaises(SharePointProbeError): client.children_page('drive-id', cursor=cursor)
            graph.assert_not_called()
        with patch('tasks.shared_documents._json_request', return_value={'value': [], '@odata.nextLink': 'https://evil.test/steal'}):
            with self.assertRaises(SharePointProbeError): client.children_page('drive-id')

    def test_search_returns_partial_state_and_remote_sort(self):
        client = self.page_client()
        with patch('tasks.shared_documents._json_request', return_value={'value': [self.file],
                '@odata.nextLink': 'https://graph.microsoft.com/v1.0/drives/drive-id/root/children?$skiptoken=next'}) as graph:
            rows, cursor, scanned = client.children_page('drive-id', query='unmatched', order='modified')
            self.assertEqual(rows, [])
            self.assertTrue(cursor)
            self.assertEqual(scanned, 1)
            self.assertIn('lastModifiedDateTime+desc', graph.call_args.args[0])

    def test_partial_navigation_keeps_auth_and_no_store_headers(self):
        with patch('tasks.shared_documents.UserDocuments') as factory:
            factory.return_value.libraries = [self.library]
            factory.return_value.library.return_value = self.library
            factory.return_value.children_page.return_value = ([], 'encrypted-next', 50)
            response = self.client.get('/work/shared-documents/?drive=drive-id&q=missing',
                                       HTTP_X_WORKHUB_DOCUMENTS='1')
            self.assertContains(response, '아직 확인하지 않은 항목이 있습니다')
            self.assertContains(response, '다음 항목 검색')
            self.assertNotContains(response, '<html')
            self.assertIn('no-store', response['Cache-Control'])
            self.assertIn('X-Workhub-Documents', response['Vary'])
        self.client.logout()
        response = self.client.get('/work/shared-documents/?drive=drive-id', HTTP_X_WORKHUB_DOCUMENTS='1')
        self.assertEqual(response.status_code, 302)
        self.assertIn('/login/', response.url)

    def test_paged_items_revalidate_selected_site(self):
        client = self.page_client()
        with patch('tasks.shared_documents._json_request', return_value={
                'value': [{**self.file, 'webUrl': 'https://evil.test/doc'}]}):
            with self.assertRaises(SharePointProbeError): client.children_page('drive-id')

    def test_parent_permission_precedes_graph(self):
        with patch('tasks.shared_documents.parent_for', side_effect=Http404), \
                patch('tasks.shared_documents.UserDocuments') as graph:
            response = self.client.get('/work/shared-documents/?kind=task&parent=999')
            self.assertEqual(response.status_code, 404)
            graph.assert_not_called()

    def test_open_revalidates_and_redirects_to_original(self):
        with patch('tasks.shared_documents.UserDocuments') as factory:
            factory.return_value.item.return_value = self.file
            response = self.client.get('/work/shared-documents/drive-id/file-id/open/')
            factory.return_value.item.assert_called_once_with('drive-id', 'file-id')
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, self.file['webUrl'])
        self.assertEqual(response['Referrer-Policy'], 'no-referrer')

    def test_failed_callback_does_not_store_connection(self):
        session = self.client.session
        session['document_oauth'] = {'user': self.user.pk + 1, 'state': 'state', 'at': 0}
        session.save()
        with patch('tasks.shared_documents.token_request') as token:
            response = self.client.get('/work/shared-documents/callback/?state=state&code=private-code')
            token.assert_not_called()
        self.assertEqual(response.status_code, 302)
        self.assertFalse(DocumentConnection.objects.exists())
        self.assertNotIn('document_oauth', self.client.session)

    def test_templates_compile(self):
        for name in ('shared_documents', 'shared_document_link'):
            get_template('tasks/' + name + '.html')

    def test_listing_renders_files_and_folders_with_distinct_actions(self):
        folder = {**self.file, 'id': 'folder-id', 'name': 'project-folder', 'folder': {}}
        folder.pop('file')
        with patch('tasks.shared_documents.UserDocuments') as factory:
            factory.return_value.libraries = [self.library]
            factory.return_value.library.return_value = self.library
            factory.return_value.children_page.return_value = ([folder, self.file], '', 2)
            response = self.client.get('/work/shared-documents/?drive=drive-id')
        self.assertContains(response, 'folder=folder-id')
        self.assertContains(response, '/drive-id/file-id/open/')
        self.assertNotContains(response, '/drive-id/folder-id/open/')

    def test_oauth_connect_is_csrf_protected(self):
        from django.test import Client
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.user)
        self.assertEqual(client.post('/work/shared-documents/connect/').status_code, 403)

    def oauth_state(self):
        session = self.client.session
        session['document_oauth'] = {'user': self.user.pk, 'state': 'good', 'verifier': 'pkce',
                                     'at': timezone.now().timestamp()}
        session.save()

    def test_callback_identity_mismatch_stops_before_site_access(self):
        self.oauth_state()
        with patch('tasks.shared_documents.token_request', return_value={
                'scope': 'User.Read Sites.Selected', 'access_token': 'access', 'refresh_token': 'refresh'}), \
                patch('tasks.shared_documents._json_request', return_value={
                    'id': TENANT, 'mail': 'different@example.com'}), \
                patch('tasks.shared_documents.site_libraries') as libraries:
            self.client.get('/work/shared-documents/callback/?state=good&code=code')
            libraries.assert_not_called()
        self.assertFalse(DocumentConnection.objects.exists())

    def test_success_callback_saves_encrypted_connection_and_cannot_replay(self):
        self.oauth_state()
        with patch('tasks.shared_documents.token_request', return_value={
                'scope': 'User.Read Sites.Selected', 'access_token': 'access', 'refresh_token': 'refresh'}) as token, \
                patch('tasks.shared_documents._json_request', return_value={
                    'id': TENANT, 'mail': self.user.email}), \
                patch('tasks.shared_documents.site_libraries', return_value=[self.library]):
            self.client.get('/work/shared-documents/callback/?state=good&code=code')
            self.client.get('/work/shared-documents/callback/?state=good&code=code')
            self.assertEqual(token.call_count, 1)
        connection = DocumentConnection.objects.get(user=self.user)
        self.assertEqual(cipher().decrypt(connection.encrypted_refresh_token.encode()), b'refresh')

    def parents(self):
        self.user.is_superuser = True
        self.user.save()
        department = Department.objects.create(name='문서팀')
        employee = Employee.objects.create(name='직원', department=department, user=self.user)
        day = timezone.localdate()
        task = Task.objects.create(title='업무', creator=self.user, assignee=employee,
                                   start_date=day, due_date=day)
        project = Project.objects.create(name='프로젝트', department=department, owner=employee,
                                         start_date=day, due_date=day)
        return task, project

    def test_same_document_links_multiple_parents_without_copy(self):
        task, project = self.parents()
        with patch('tasks.shared_documents.UserDocuments') as factory:
            factory.return_value.item.return_value = self.file
            factory.return_value.config.site_url = SITE
            for kind, parent in [('task', task), ('project', project), ('task', task)]:
                response = self.client.post('/work/shared-documents/drive-id/file-id/link/',
                                            {'kind': kind, kind: parent.pk})
                self.assertEqual(response.status_code, 302)
        self.assertEqual(SharedDocument.objects.count(), 1)
        self.assertEqual(SharedDocumentLink.objects.count(), 2)

    def test_revoked_link_hides_original_name(self):
        task, _ = self.parents()
        doc = SharedDocument.objects.create(site_url=SITE, drive_id='drive-id', item_id='file-id')
        SharedDocumentLink.objects.create(document=doc, task=task, creator=self.user)
        with patch('tasks.shared_documents.UserDocuments') as factory:
            factory.return_value.config.site_url = SITE
            factory.return_value.libraries = [self.library]
            factory.return_value.item.side_effect = SharePointProbeError('denied')
            response = self.client.get(f'/work/shared-documents/?kind=task&parent={task.pk}')
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'proposal.docx')
        self.assertContains(response, '열람 가능한 연결 문서가 없습니다')

    def folder_row(self, **extra):
        row = {**self.file, 'id': 'folder-id', 'name': '교육 자료', 'folder': {}, 'eTag': '"version-1"',
               'parentReference': {'driveId': 'drive-id'}}
        row.pop('file')
        row.update(extra)
        return row

    def test_folder_name_validation(self):
        from .shared_documents import FolderForm
        for name in ('../secret', 'a/b', 'a\\b', 'a*', 'CON', 'NUL.txt', 'name.', '~$temp'):
            self.assertFalse(FolderForm({'name': name}).is_valid(), name)
        self.assertTrue(FolderForm({'name': '2027 제품 교육'}).is_valid())

    def test_staff_cannot_manage_folder_even_with_sharepoint_access(self):
        with patch('tasks.shared_documents.UserDocuments') as factory:
            for method in (self.client.get, self.client.post):
                self.assertEqual(method('/work/shared-documents/drive-id/folder/').status_code, 403)
            factory.assert_not_called()

    def test_folder_creation_confirmation_is_single_use(self):
        self.parents()
        with patch('tasks.shared_documents.UserDocuments') as factory:
            factory.return_value.library.return_value = self.library
            factory.return_value.create_folder.return_value = {'id': 'new-folder'}
            response = self.client.get('/work/shared-documents/drive-id/folder/')
            proof = response.context['proof']
            payload = {'proof': proof, 'name': '교육', 'action': 'create', 'folder': ''}
            response = self.client.post('/work/shared-documents/drive-id/folder/', payload)
            self.assertEqual(response.status_code, 302)
            self.client.post('/work/shared-documents/drive-id/folder/', payload)
            factory.return_value.create_folder.assert_called_once_with('drive-id', '', '교육')

    def test_create_from_access_management_returns_to_new_folder_access(self):
        self.parents()
        with patch('tasks.shared_documents.UserDocuments') as factory:
            factory.return_value.library.return_value=self.library
            factory.return_value.create_folder.return_value={'id':'new-folder'}
            response=self.client.get('/work/shared-documents/drive-id/folder/?origin=permissions')
            self.assertContains(response,'name="origin" value="permissions"')
            response=self.client.post('/work/shared-documents/drive-id/folder/',
                {'proof':response.context['proof'],'name':'보안기술팀','action':'create','folder':'','origin':'permissions'})
            self.assertEqual(response.url,'/work/shared-documents/drive-id/new-folder/permissions/?edit=1')

    def test_nonempty_folder_delete_never_calls_write(self):
        self.parents()
        with patch('tasks.shared_documents.UserDocuments') as factory, patch('tasks.shared_documents.graph_write') as write:
            factory.return_value.library.return_value = self.library
            factory.return_value.item.return_value = self.folder_row()
            factory.return_value.children.return_value = [self.file]
            response = self.client.get('/work/shared-documents/drive-id/folder/?action=delete&folder=folder-id')
            self.assertContains(response, '자료가 있는 폴더는 삭제할 수 없습니다')
            response = self.client.post('/work/shared-documents/drive-id/folder/',
                {'action': 'delete', 'folder': 'folder-id', 'proof': response.context['proof']})
            self.assertEqual(response.status_code, 200)
            write.assert_not_called()

    def test_empty_folder_delete_uses_etag_and_no_permanent_delete(self):
        self.parents()
        with patch('tasks.shared_documents.UserDocuments') as factory, patch('tasks.shared_documents.graph_write') as write:
            factory.return_value.library.return_value = self.library
            factory.return_value.item.return_value = self.folder_row()
            factory.return_value.children.return_value = []
            factory.return_value.token = 'delegated'
            response = self.client.get('/work/shared-documents/drive-id/folder/?action=delete&folder=folder-id')
            self.client.post('/work/shared-documents/drive-id/folder/',
                {'action': 'delete', 'folder': 'folder-id', 'proof': response.context['proof']})
            self.assertEqual(write.call_args.args[2], 'DELETE')
            self.assertEqual(write.call_args.kwargs['etag'], '"version-1"')
            self.assertNotIn('permanent', write.call_args.args[0])

    def test_stale_folder_confirmation_does_not_write(self):
        self.parents()
        with patch('tasks.shared_documents.UserDocuments') as factory, patch('tasks.shared_documents.graph_write') as write:
            factory.return_value.library.return_value = self.library
            factory.return_value.item.return_value = self.folder_row()
            response = self.client.get('/work/shared-documents/drive-id/folder/?action=rename&folder=folder-id')
            factory.return_value.item.return_value = self.folder_row(eTag='"version-2"')
            response = self.client.post('/work/shared-documents/drive-id/folder/',
                {'action': 'rename', 'folder': 'folder-id', 'name': '변경', 'proof': response.context['proof']})
            self.assertContains(response, '폴더가 변경되었습니다')
            write.assert_not_called()

    def test_folder_get_does_not_write_and_root_cannot_be_deleted(self):
        self.parents()
        with patch('tasks.shared_documents.UserDocuments') as factory, patch('tasks.shared_documents.graph_write') as write:
            factory.return_value.library.return_value = self.library
            factory.return_value.item.return_value = self.folder_row(root={})
            self.assertEqual(self.client.get('/work/shared-documents/drive-id/folder/?action=delete&folder=folder-id').status_code, 404)
            write.assert_not_called()

    def test_signed_confirmation_cannot_change_target(self):
        self.parents()
        with patch('tasks.shared_documents.UserDocuments') as factory, patch('tasks.shared_documents.graph_write') as write:
            factory.return_value.library.return_value = self.library
            factory.return_value.item.return_value = self.folder_row()
            factory.return_value.children.return_value = []
            response = self.client.get('/work/shared-documents/drive-id/folder/?action=rename&folder=folder-id')
            response = self.client.post('/work/shared-documents/drive-id/folder/',
                {'action': 'delete', 'folder': 'folder-id', 'proof': response.context['proof']})
            self.assertContains(response, '요청이 만료되었습니다')
            write.assert_not_called()

    def test_ancestry_uses_ids_and_rejects_cycles(self):
        client = object.__new__(UserDocuments)
        rows = [self.folder_row(id='child', parentReference={'id': 'parent', 'driveId': 'drive-id'}),
                self.folder_row(id='parent', name='교육', parentReference={'id': 'root', 'driveId': 'drive-id'}),
                self.folder_row(id='root', root={})]
        with patch.object(client, 'item', side_effect=rows):
            self.assertEqual([r['id'] for r in client.ancestry('drive-id', 'child')], ['parent', 'child'])
        with patch.object(client, 'item', return_value=self.folder_row(parentReference={'id': 'loop'})):
            with self.assertRaises(SharePointProbeError): client.ancestry('drive-id', 'loop')

    def test_root_parent_path_avoids_redundant_root_lookup(self):
        client = object.__new__(UserDocuments)
        row = self.folder_row(parentReference={'id': 'root-id', 'driveId': 'drive-id',
                                               'path': '/drives/drive-id/root:'})
        with patch.object(client, 'item', return_value=row) as item:
            self.assertEqual(len(client.ancestry('drive-id', 'folder-id')), 1)
            item.assert_called_once_with('drive-id', 'folder-id')

    def test_permission_panel_does_not_claim_complete_access_list(self):
        with patch('tasks.shared_documents.UserDocuments') as factory:
            factory.return_value.item.return_value = self.file
            factory.return_value.permissions.return_value = [{'names': '<script>bad</script>', 'roles': '열람', 'inherited': True}]
            response = self.client.get('/work/shared-documents/drive-id/file-id/access/')
            self.assertContains(response, '전체 접근자를 모두 표시하는 목록은 아닙니다')
            self.assertNotContains(response, '<script>bad</script>')

    def test_folder_post_is_csrf_protected(self):
        from django.test import Client
        self.parents()
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.user)
        self.assertEqual(client.post('/work/shared-documents/drive-id/folder/', {'name': 'new'}).status_code, 403)

    def test_folder_links_and_breadcrumbs_render_without_separate_open_button(self):
        with patch('tasks.shared_documents.UserDocuments') as factory:
            factory.return_value.library.return_value = self.library
            factory.return_value.libraries = [self.library]
            factory.return_value.children_page.return_value = ([self.folder_row()], '', 1)
            factory.return_value.ancestry.return_value = [{'id': 'parent', 'name': '상위'}, {'id': 'child', 'name': '현재'}]
            response = self.client.get('/work/shared-documents/?drive=drive-id&folder=child')
            self.assertContains(response, '현재 폴더 경로')
            self.assertContains(response, '↑ 상위 폴더')
            self.assertNotContains(response, '>폴더 열기</a>')
            self.assertContains(response, 'shared-folder-link')
