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
        self.user = get_user_model().objects.create_user('document-user', email='staff@example.com')
        self.client.force_login(self.user)
        self.config = ProbeConfig.from_settings()
        self.library = {'id': 'drive-id', 'name': '공통', 'webUrl': SITE + '/DocLib'}
        self.file = {'id': 'file-id', 'name': 'proposal.docx', 'webUrl': SITE + '/DocLib/proposal.docx',
                     'file': {}, 'parentReference': {'driveId': 'drive-id'}, 'size': 10}

    def connection(self, **values):
        data = dict(user=self.user, tenant=TENANT, client=CLIENT, subject=TENANT,
                    email=self.user.email, encrypted_refresh_token=cipher().encrypt(b'refresh').decode())
        data.update(values)
        return DocumentConnection.objects.create(**data)

    def test_no_connection_never_uses_application_token(self):
        with patch('tasks.shared_documents.token_request') as token:
            with self.assertRaises(SharePointProbeError):
                UserDocuments(self.user)
            token.assert_not_called()

    def test_refresh_is_delegated_and_rotated_encrypted(self):
        connection = self.connection()
        with patch('tasks.shared_documents.token_request', return_value={
                'scope': 'User.Read Sites.Selected', 'access_token': 'delegated', 'refresh_token': 'rotated'}) as token, \
                patch('tasks.shared_documents.site_libraries', return_value=[self.library]) as libraries:
            client = UserDocuments(self.user)
        self.assertEqual(token.call_args.args[1]['grant_type'], 'refresh_token')
        libraries.assert_called_once_with(self.config, 'delegated')
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
            factory.return_value.children.return_value = [folder, self.file]
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
