from types import SimpleNamespace
from unittest.mock import patch, Mock
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, Client
from .models import SystemAudit
from .sharepoint_probe import SharePointProbeError
from .shared_files import UploadForm
from .shared_file_http import transfer_url, upload, download, CHUNK


class SharedFilesTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user('file-editor', email='editor@example.com')
        self.client.force_login(self.user)
        self.library = {'id': 'drive', 'name': '전사 공통', 'webUrl': 'https://example.sharepoint.com/sites/work/docs'}
        self.file = {'id': 'file', 'name': '보고서.txt', 'file': {}, 'size': 3, 'eTag': '"v1"',
                     'parentReference': {'driveId': 'drive', 'id': 'old', 'path': '/drives/drive/root:/old'}}
        self.folder = {'id': 'dest', 'name': '교육', 'folder': {}, 'parentReference': {'driveId': 'drive', 'id': 'root', 'path': '/drives/drive/root:'}}
        self.factory_patch = patch('tasks.shared_files.UserDocuments')
        self.factory = self.factory_patch.start()
        self.addCleanup(self.factory_patch.stop)
        self.docs = self.factory.return_value
        self.docs.library.return_value = self.library
        self.docs.item.side_effect = lambda drive, item: self.folder if item == 'dest' else self.file
        self.docs.ancestry.return_value = [{'id': 'dest', 'name': '교육'}]
        self.docs.children_page.return_value = ([self.folder], '', 1)
        self.docs.token = 'delegated'

    def url(self, action):
        return '/work/shared-documents/drive/file/manage/' + action + '/'

    def test_get_is_readonly_and_audit_records_actor_time_and_name_changes(self):
        with patch('tasks.shared_files.graph_write') as write:
            response = self.client.get(self.url('rename'))
            write.assert_not_called()
            response = self.client.post(self.url('rename'), {'proof': response.context['proof'], 'name': '수정.txt'})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(write.call_args.kwargs['etag'], '"v1"')
        self.assertEqual(write.call_args.args[1], 'delegated')
        event = SystemAudit.objects.get(target_type='SharePointFile')
        self.assertEqual(event.actor_id_snapshot, self.user.pk)
        self.assertEqual(event.before['파일명'], '보고서.txt')
        self.assertEqual(event.after['파일명'], '수정.txt')
        self.assertIsNotNone(event.created_at)

    def test_stale_replayed_or_action_changed_proof_never_writes(self):
        for action, changed in [('rename', 'etag'), ('delete', 'action')]:
            response = self.client.get(self.url('rename'))
            if changed == 'etag':
                self.file['eTag'] = '"v2"'
            with patch('tasks.shared_files.graph_write') as write:
                response = self.client.post(self.url(action), {'proof': response.context['proof'], 'name': '수정.txt'})
                write.assert_not_called()
                self.assertContains(response, '요청이 만료되거나')
        response = self.client.get(self.url('delete'))
        data = {'proof': response.context['proof']}
        with patch('tasks.shared_files.graph_write') as write:
            self.assertEqual(self.client.post(self.url('delete'), data).status_code, 302)
            self.client.post(self.url('delete'), data)
            self.assertEqual(write.call_count, 1)
            self.assertEqual(write.call_args.args[2], 'DELETE')

    def test_rename_keeps_extension_and_denied_changes_do_not_claim_success(self):
        response = self.client.get(self.url('rename'))
        with patch('tasks.shared_files.graph_write') as write:
            response = self.client.post(self.url('rename'), {'proof': response.context['proof'], 'name': '변경.exe'})
            self.assertContains(response, '확장자는 유지')
            write.assert_not_called()
        with patch('tasks.shared_files.graph_write', side_effect=SharePointProbeError('권한이 없습니다.')):
            response = self.client.post(self.url('rename'), {'proof': response.context['proof'], 'name': '변경.txt'})
            self.assertContains(response, '권한이 없습니다')
            self.assertFalse(SystemAudit.objects.filter(target_type='SharePointFile').exists())

    def test_move_uses_browsed_folder_and_audits_old_new_location(self):
        response = self.client.get(self.url('move') + '?to=dest')
        self.assertContains(response, '이 폴더로 이동')
        with patch('tasks.shared_files.graph_write') as write:
            response = self.client.post(self.url('move'), {'proof': response.context['proof'], 'to': 'dest'})
            self.assertEqual(response.status_code, 302)
            self.assertEqual(write.call_args.args[3]['parentReference'], {'id': 'dest'})
        event = SystemAudit.objects.get(target_type='SharePointFile')
        self.assertEqual((event.before['폴더'], event.after['폴더']), ('old', 'dest'))

    def test_move_destination_is_bound_to_proof_and_cannot_be_a_file(self):
        response = self.client.get(self.url('move') + '?to=dest')
        with patch('tasks.shared_files.graph_write') as write:
            response = self.client.post(self.url('move'), {'proof': response.context['proof'], 'to': ''})
            self.assertContains(response, '요청이 만료되거나')
            write.assert_not_called()
        self.assertEqual(self.client.get(self.url('move') + '?to=file').status_code, 404)

    def test_other_library_not_found_before_write(self):
        from django.http import Http404
        self.docs.library.side_effect = Http404
        with patch('tasks.shared_files.graph_write') as write:
            self.assertEqual(self.client.get(self.url('delete')).status_code, 404)
            write.assert_not_called()

    def test_upload_keeps_original_name_and_logs_only_after_success(self):
        url = '/work/shared-documents/drive/upload/'
        response = self.client.get(url)
        with patch('tasks.shared_files.shared_file_http.upload', return_value=self.file) as send:
            response = self.client.post(url, {'proof': response.context['proof'], 'file': SimpleUploadedFile('보고서.txt', b'abc')})
            self.assertEqual(response.status_code, 302)
            self.assertEqual(send.call_args.args[3].name, '보고서.txt')
        event = SystemAudit.objects.get(target_type='SharePointFile')
        self.assertEqual(event.action, 'create')
        self.assertEqual(event.after['용량'], 3)
        self.assertNotIn('delegated', str(event.after))

    def test_upload_validation_and_csrf(self):
        for name, content in [('script.exe', b'123'), ('zero.txt', b''), ('CON.txt', b'123')]:
            form = UploadForm(files={'file': SimpleUploadedFile(name, content)})
            self.assertFalse(form.is_valid())
        strict = Client(enforce_csrf_checks=True)
        strict.force_login(self.user)
        with patch('tasks.shared_files.graph_write') as write:
            self.assertEqual(strict.post(self.url('delete'), {}).status_code, 403)
            write.assert_not_called()
        self.client.logout()
        self.assertEqual(self.client.get('/work/shared-documents/drive/file/download/').status_code, 302)

    def test_new_version_preserves_document_id_and_audits_content_replacement(self):
        response = self.client.get(self.url('replace'))
        with patch('tasks.shared_files.shared_file_http.upload', return_value={**self.file, 'size': 7, 'eTag': '"v2"'}) as upload:
            response = self.client.post(self.url('replace'), {'proof': response.context['proof'], 'file': SimpleUploadedFile('수정본.txt', b'updated')})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(upload.call_args.kwargs['existing']['id'], 'file')
        event = SystemAudit.objects.get(target_type='SharePointFile')
        self.assertEqual(event.action, 'update')
        self.assertEqual((event.before['용량'], event.after['용량']), (3, 7))
        self.assertEqual((event.before['버전'], event.after['버전']), ('"v1"', '"v2"'))

    def test_confirmation_is_bound_to_logged_in_employee(self):
        response = self.client.get(self.url('delete'))
        other = get_user_model().objects.create_user('another-editor')
        self.client.force_login(other)
        with patch('tasks.shared_files.graph_write') as write:
            self.client.post(self.url('delete'), {'proof': response.context['proof']})
            write.assert_not_called()

    def test_administrator_log_supports_file_names_and_is_not_employee_visible(self):
        response = self.client.get(self.url('rename'))
        with patch('tasks.shared_files.graph_write'):
            self.client.post(self.url('rename'), {'proof': response.context['proof'], 'name': '수정.txt'})
        self.assertEqual(self.client.get('/work/system-log/').status_code, 403)
        self.user.is_superuser = True
        self.user.save()
        response = self.client.get('/work/system-log/?q=보고서.txt')
        self.assertContains(response, '공동 문서 파일')
        self.assertContains(response, 'file-editor')
        self.assertContains(response, '수정.txt')

    def test_download_preserves_binary_bytes_filename_and_closes_upstream(self):
        upstream = Mock()
        upstream.iter_content.return_value = iter([b'\x89PNG\r\n', b'\x00\xff'])
        self.file['name'] = '증빙.png'
        with patch('tasks.shared_files.shared_file_http.download', return_value=upstream):
            response = self.client.get('/work/shared-documents/drive/file/download/')
            self.assertIn('attachment;', response['Content-Disposition'])
            self.assertIn('.png', response['Content-Disposition'])
            self.assertEqual(b''.join(response.streaming_content), b'\x89PNG\r\n\x00\xff')
            upstream.close.assert_called()


class FileTransferTests(TestCase):
    def setUp(self):
        self.config = SimpleNamespace(site_url='https://example.sharepoint.com/sites/work')
        self.client = Mock(config=self.config, token='secret-delegated')
        self.row = {'id': 'file', 'name': 'sample.txt', 'size': 3, 'file': {}, 'eTag': '"v1"'}

    def response(self, status=201, data=None):
        response = Mock(status_code=status, headers={})
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.json.return_value = data or self.row
        return response

    def test_transfer_urls_reject_external_hosts_credentials_and_non_tls(self):
        for url in ['http://example.sharepoint.com/a', 'https://evil.test/a', 'https://example.sharepoint.com.evil.test/a', 'https://u:p@example.sharepoint.com/a', 'https://example.sharepoint.com:8443/a', 'https://127.0.0.1/a']:
            self.assertFalse(transfer_url(url, self.config), url)
        self.assertTrue(transfer_url('https://example.sharepoint.com/upload', self.config))

    def test_upload_binary_request_is_conflict_fail_and_keeps_original_bytes(self):
        file = SimpleUploadedFile('sample.txt', b'abc')
        with patch('tasks.shared_file_http.requests.put', return_value=self.response()) as put:
            self.assertEqual(upload(self.client, 'drive', '', file), self.row)
            self.assertTrue(put.call_args.args[0].endswith(':/content?@microsoft.graph.conflictBehavior=fail'))
            self.assertEqual(put.call_args.kwargs['headers']['If-None-Match'], '*')
            self.assertEqual(put.call_args.kwargs['headers']['Authorization'], 'Bearer secret-delegated')
            self.assertEqual(put.call_args.kwargs['data'].read(), b'abc')
            self.assertFalse(put.call_args.kwargs['allow_redirects'])

    def test_upload_conflict_and_redirect_never_retry_or_follow(self):
        for status in (409, 302, 403):
            with patch('tasks.shared_file_http.requests.put', return_value=self.response(status)) as put:
                with self.assertRaises(SharePointProbeError):
                    upload(self.client, 'drive', '', SimpleUploadedFile('sample.txt', b'abc'))
                self.assertEqual(put.call_count, 1)

    def test_new_version_keeps_identity_and_uses_if_match(self):
        with patch('tasks.shared_file_http.requests.put', return_value=self.response()) as put:
            upload(self.client, 'drive', '', SimpleUploadedFile('different.txt', b'abc'), existing=self.row)
            self.assertTrue(put.call_args.args[0].endswith('/items/file/content'))
            self.assertEqual(put.call_args.kwargs['headers']['If-Match'], '\"v1\"')
            self.assertNotIn('If-None-Match', put.call_args.kwargs['headers'])

    def test_download_redirect_drops_bearer_and_rejects_external_destination(self):
        redirect = self.response(302)
        redirect.headers = {'Location': 'https://example.sharepoint.com/download'}
        content = self.response(200)
        with patch('tasks.shared_file_http.requests.get', side_effect=[redirect, content]) as get:
            self.assertIs(download(self.client, 'drive', self.row), content)
            self.assertEqual(get.call_args_list[0].kwargs['headers']['Authorization'], 'Bearer secret-delegated')
            self.assertNotIn('headers', get.call_args_list[1].kwargs)
        redirect.headers = {'Location': 'https://evil.test/file'}
        with patch('tasks.shared_file_http.requests.get', return_value=redirect) as get:
            with self.assertRaises(SharePointProbeError): download(self.client, 'drive', self.row)
            self.assertEqual(get.call_count, 1)
