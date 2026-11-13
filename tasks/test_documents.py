import io
import tempfile
from pathlib import Path
from unittest.mock import Mock, patch
from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, SimpleTestCase, override_settings
from django.utils import timezone
from .models import Department, Employee, Task, TaskParticipation, Project, WorkRequest, WorkDocument, PeriodReport, ReportFile
from .drive_storage import WorkhubStorage, DriveClient, RepositoryError


@override_settings(WORKHUB_REQUIRE_HIWORKS=False,WORKHUB_SCHEDULER_ENABLED=False,
    PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'],
    GOOGLE_DRIVE_CREDENTIALS_FILE='/etc/secrets/example.json',GOOGLE_DRIVE_SHARED_DRIVE_ID='shared-drive')
class DocumentTests(TestCase):
    def image_bytes(self):
        from PIL import Image
        out=io.BytesIO();Image.new('RGB',(900,500),'green').save(out,format='PNG');return out.getvalue()
    def test_custom_image_title_download_keeps_extension_and_bytes(self):
        doc=self.doc();doc.name='첨부 증빙';doc.save();data=self.image_bytes()
        with patch('tasks.drive_storage.WorkhubStorage._open',return_value=ContentFile(data)):
            response=self.client.get(f'/work/documents/{doc.pk}/download/')
            self.assertEqual(b''.join(response.streaming_content),data)
        self.assertIn('.png',response['Content-Disposition']);self.assertIn('attachment',response['Content-Disposition'])
    def test_download_uses_original_name_without_changing_display_title(self):
        from urllib.parse import unquote
        doc=self.doc(original_name='실제 견적서.pdf');doc.name='구매 검토용';doc.save()
        with patch('tasks.drive_storage.WorkhubStorage._open',return_value=ContentFile(b'%PDF-original')):
            response=self.client.get(f'/work/documents/{doc.pk}/download/')
            self.assertEqual(b''.join(response.streaming_content),b'%PDF-original')
        header=unquote(response['Content-Disposition'])
        self.assertIn('실제 견적서.pdf',header);self.assertNotIn('구매 검토용',header)
        doc.refresh_from_db();self.assertEqual(doc.name,'구매 검토용')
        page=self.client.get(f'/tasks/{self.task.pk}/')
        self.assertContains(page,'구매 검토용');self.assertContains(page,'파일명: 실제 견적서.pdf')

    def test_original_jpeg_suffix_is_preserved_and_bad_image_suffix_repaired(self):
        from urllib.parse import unquote
        doc=self.doc(original_name='original.jpeg');doc.name='표시 제목';doc.save()
        for original,expected in [('original.jpeg','original.jpeg'),('wrong.pdf','wrong.jpg')]:
            doc.original_name=original;doc.save()
            with patch('tasks.drive_storage.WorkhubStorage._open',return_value=ContentFile(b'\xff\xd8\xffimage')):
                response=self.client.get(f'/work/documents/{doc.pk}/download/')
                self.assertEqual(b''.join(response.streaming_content),b'\xff\xd8\xffimage')
            self.assertIn(expected,unquote(response['Content-Disposition']))

    def test_image_preview_decodes_bounded_jpeg_and_rechecks_acl(self):
        from PIL import Image
        doc=self.doc();doc.name='이미지 증빙';doc.save()
        self.assertContains(self.client.get(f'/tasks/{self.task.pk}/'),'이미지 미리보기')
        with patch('tasks.drive_storage.WorkhubStorage._open',return_value=ContentFile(self.image_bytes())):
            response=self.client.get(f'/work/documents/{doc.pk}/preview/')
        self.assertEqual(response['Content-Type'],'image/jpeg');self.assertIn('no-store',response['Cache-Control'])
        with Image.open(io.BytesIO(response.content)) as im:self.assertLessEqual(im.width,640);self.assertLessEqual(im.height,480)
        self.client.force_login(self.other)
        with patch('tasks.drive_storage.WorkhubStorage._open') as opened:
            self.assertEqual(self.client.get(f'/work/documents/{doc.pk}/preview/').status_code,404);opened.assert_not_called()
    def test_invalid_image_cannot_be_served_inline(self):
        doc=self.doc()
        with patch('tasks.drive_storage.WorkhubStorage._open',return_value=ContentFile(b'<svg onload="alert(1)"/>')):
            response=self.client.get(f'/work/documents/{doc.pk}/preview/')
        self.assertEqual(response.status_code,422);self.assertNotIn(b'<svg',response.content)
    def test_original_filename_saved_separately_from_display_title(self):
        with patch('tasks.drive_storage.DriveClient.upload',return_value='drive/file1'):
            self.assertEqual(self.client.post(self.url(),{'kind':'reference','name':'검토자료','file':self.file()}).status_code,302)
        self.assertEqual(WorkDocument.objects.get().original_name,'소개자료.pdf')
    def setUp(self):
        init=patch('tasks.drive_storage.DriveClient.__init__',return_value=None);init.start();self.addCleanup(init.stop)
        U=get_user_model();self.root=U.objects.create_superuser('root')
        self.user=U.objects.create_user('worker');self.other=U.objects.create_user('other')
        self.team=Department.objects.create(name='자료팀');other=Department.objects.create(name='자료다른팀')
        self.emp=Employee.objects.create(name='직원',user=self.user,department=self.team)
        Employee.objects.create(name='다른직원',user=self.other,department=other)
        self.day=timezone.localdate()
        self.task=Task.objects.create(title='자료업무',creator=self.root,assignee=self.emp,workflow_version=2,start_date=self.day,due_date=self.day)
        TaskParticipation.objects.create(task=self.task,employee=self.emp)
        self.client.force_login(self.user)
    def url(self,kind='task',pk=None):return f'/work/documents/{kind}/{pk or self.task.pk}/add/'
    def file(self):return SimpleUploadedFile('소개자료.pdf',b'%PDF-reference')
    def doc(self,**kw):
        return WorkDocument.objects.create(task=self.task,name='자료.pdf',file='drive/abc',uploader=self.user,uploader_name='직원',**kw)
    def test_task_upload_uses_drive_and_lists_metadata(self):
        with patch('tasks.drive_storage.DriveClient.upload',return_value='drive/file1') as upload:
            r=self.client.post(self.url(),{'kind':'reference','file':self.file()})
        self.assertEqual(r.status_code,302);upload.assert_called_once()
        doc=WorkDocument.objects.get();self.assertEqual(doc.file.name,'drive/file1');self.assertEqual(doc.size,14)
        r=self.client.get(f'/tasks/{self.task.pk}/');self.assertContains(r,'소개자료.pdf');self.assertContains(r,'자료 추가')
    def test_outsider_cannot_upload_download_or_see_file_names(self):
        doc=self.doc();self.client.force_login(self.other)
        self.assertEqual(self.client.get(self.url()).status_code,404)
        with patch('tasks.drive_storage.DriveClient.open') as cloud:
            self.assertEqual(self.client.get(f'/work/documents/{doc.pk}/download/').status_code,404)
            cloud.assert_not_called()
    def test_legacy_task_page_also_lists_private_documents(self):
        self.task.workflow_version=1;self.task.save();self.doc()
        r=self.client.get(f'/tasks/{self.task.pk}/')
        self.assertContains(r,'업무자료');self.assertContains(r,'자료.pdf');self.assertContains(r,'자료 추가')
    def test_download_is_authenticated_private_attachment(self):
        doc=self.doc()
        with patch('tasks.drive_storage.WorkhubStorage._open',return_value=ContentFile(b'file')):
            r=self.client.get(f'/work/documents/{doc.pk}/download/')
            self.assertEqual(b''.join(r.streaming_content),b'file')
        self.assertIn('no-store',r['Cache-Control']);self.assertIn('attachment',r['Content-Disposition'])
    def test_link_only_has_name_and_safe_scheme(self):
        self.assertEqual(self.client.post(self.url(),{'kind':'reference','name':'공유문서','link':'https://example.com/doc'}).status_code,302)
        r=self.client.post(self.url(),{'kind':'reference','name':'위험','link':'javascript:alert(1)'})
        self.assertEqual(r.status_code,200);self.assertIn('link',r.context['form'].errors)
        self.assertEqual(WorkDocument.objects.count(),1)
    def test_missing_both_or_both_file_link_rejected(self):
        for values in [{},{'file':self.file(),'link':'https://example.com'}]:
            r=self.client.post(self.url(),{'kind':'reference',**values})
            self.assertEqual(r.status_code,200);self.assertTrue(r.context['form'].non_field_errors())
    def test_size_extension_and_empty_validation(self):
        for value in [SimpleUploadedFile('bad.exe',b'bin'),SimpleUploadedFile('empty.pdf',b'')]:
            r=self.client.post(self.url(),{'kind':'reference','file':value});self.assertIn('file',r.context['form'].errors)
        with override_settings(WORKHUB_DOCUMENT_MAX_BYTES=2):
            r=self.client.post(self.url(),{'kind':'reference','file':self.file()});self.assertIn('file',r.context['form'].errors)
    def test_unconfigured_files_disabled_but_links_work(self):
        with override_settings(GOOGLE_DRIVE_CREDENTIALS_FILE='',GOOGLE_DRIVE_SHARED_DRIVE_ID=''):
            r=self.client.post(self.url(),{'kind':'reference','file':self.file()});self.assertIn('file',r.context['form'].errors)
            self.assertEqual(self.client.post(self.url(),{'kind':'reference','name':'링크','link':'https://example.com'}).status_code,302)
    def test_cloud_failure_is_form_error_not_500(self):
        with patch('tasks.drive_storage.DriveClient.upload',side_effect=RepositoryError('연결 오류')):
            r=self.client.post(self.url(),{'kind':'reference','file':self.file()})
        self.assertEqual(r.status_code,200);self.assertContains(r,'연결 오류');self.assertFalse(WorkDocument.objects.exists())
    def test_archive_requires_post_owner_and_reason_preserves_file(self):
        doc=self.doc();url=f'/work/documents/{doc.pk}/archive/'
        self.assertEqual(self.client.get(url).status_code,405)
        self.client.post(url,{});doc.refresh_from_db();self.assertIsNone(doc.archived_at)
        self.client.post(url,{'reason':'중복 자료'});doc.refresh_from_db();self.assertEqual(doc.file.name,'drive/abc');self.assertIsNotNone(doc.archived_at)
        self.assertEqual(self.client.get(f'/work/documents/{doc.pk}/download/').status_code,404)
    def test_csrf_protection_on_upload_and_archive(self):
        from django.test import Client
        c=Client(enforce_csrf_checks=True);c.force_login(self.user)
        self.assertEqual(c.post(self.url(),{'kind':'reference','name':'x','link':'https://example.com'}).status_code,403)
        self.assertEqual(c.post(f'/work/documents/{self.doc().pk}/archive/',{'reason':'x'}).status_code,403)
    def test_request_creation_attachment_and_failed_upload_rollback(self):
        data={'title':'소개자료 검토','description':'검토 요청','request_kind':'cooperation','department':self.team.pk,'requested_due_date':str(self.day)}
        with patch('tasks.drive_storage.DriveClient.upload',return_value='drive/request1'):
            r=self.client.post('/intake/new/',{**data,'attachment':self.file()})
        self.assertEqual(r.status_code,302);obj=WorkRequest.objects.get();self.assertEqual(obj.documents.count(),1)
        self.assertContains(self.client.get(f'/intake/{obj.pk}/'),'소개자료.pdf')
        with patch('tasks.drive_storage.DriveClient.upload',side_effect=RepositoryError('연결 오류')):
            r=self.client.post('/intake/new/',{**data,'title':'실패 요청','attachment':self.file()})
        self.assertEqual(r.status_code,200);self.assertFalse(WorkRequest.objects.filter(title='실패 요청').exists())

    def test_direct_request_file_upload_and_failure_rolls_back_task(self):
        self.client.force_login(self.root)
        data={'purpose':'request','title':'소개자료 확인','assignee':self.emp.pk,'start_date':self.day,'due_date':self.day,
              'importance':'normal','priority':3,'status':'todo','allocation':'shared','revision':0,'no_approval':'on','assignment_channels_present':'1'}
        with patch('tasks.drive_storage.DriveClient.upload',return_value='drive/direct1'):
            r=self.client.post('/work/new/',{**data,'attachment':self.file()})
        self.assertEqual(r.status_code,302);task=Task.objects.get(title='소개자료 확인');self.assertEqual(task.documents.count(),1)
        with patch('tasks.drive_storage.DriveClient.upload',side_effect=RepositoryError('연결 오류')):
            r=self.client.post('/work/new/',{**data,'title':'실패직접요청','attachment':self.file()})
        self.assertEqual(r.status_code,200);self.assertFalse(Task.objects.filter(title='실패직접요청').exists())
    def test_report_new_file_routes_to_drive_and_read_checks_report_acl(self):
        report=PeriodReport.objects.create(author=self.user,reviewer=self.root,period_start=self.day,period_end=self.day,summary='보고')
        with patch('tasks.drive_storage.DriveClient.upload',return_value='drive/report1') as upload:
            f=ReportFile.objects.create(report=report,name='보고.pdf',file=self.file())
        upload.assert_called_once();self.assertEqual(f.file.name,'drive/report1')
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(f'/work/reports/files/{f.pk}/').status_code,404)

    def test_individual_allocation_reuses_one_upload_with_separate_task_acl(self):
        self.client.force_login(self.root);other_emp=Employee.objects.get(user=self.other)
        data={'purpose':'request','title':'개별 자료 검토','assignee':self.emp.pk,'collaborators':[other_emp.pk],
              'start_date':self.day,'due_date':self.day,'importance':'normal','priority':3,'status':'todo',
              'allocation':'individual','revision':0,'no_approval':'on','assignment_channels_present':'1','attachment':self.file()}
        with patch('tasks.drive_storage.DriveClient.upload',return_value='drive/shared-reference') as upload:
            self.assertEqual(self.client.post('/work/new/',data).status_code,302)
        upload.assert_called_once();docs=list(WorkDocument.objects.filter(name='소개자료.pdf'))
        self.assertEqual(len(docs),2);self.assertEqual({d.file.name for d in docs},{'drive/shared-reference'})
        self.client.force_login(self.other)
        first=next(d for d in docs if d.task.assignee_id==self.emp.pk)
        self.assertEqual(self.client.get(f'/work/documents/{first.pk}/download/').status_code,404)
    def test_admin_connection_check_and_staff_denial(self):
        self.assertEqual(self.client.get('/work/documents/storage/').status_code,403)
        self.client.force_login(self.root)
        with patch('tasks.documents.DriveClient.check',return_value='Workhub 업무자료'):
            r=self.client.post('/work/documents/storage/')
        self.assertContains(r,'연결 확인 완료');self.assertNotContains(r,'example.json')
    def test_project_upload_scope_and_assigned_request_files(self):
        self.client.force_login(self.root)
        project=Project.objects.create(name='자료프로젝트',department=self.team,owner=self.emp,start_date=self.day,due_date=self.day)
        r=self.client.post(self.url('project',project.pk),{'kind':'result','name':'산출물','link':'https://example.com'})
        self.assertEqual(r.status_code,302);self.assertContains(self.client.get(f'/projects/{project.pk}/'),'산출물')
        obj=WorkRequest.objects.create(title='원본요청',creator=self.user,creator_name='직원',department=self.team,requested_due_date=self.day,task=self.task)
        WorkDocument.objects.create(work_request=obj,name='원본소개',link='https://example.com',uploader=self.user,uploader_name='직원')
        self.assertContains(self.client.get(f'/tasks/{self.task.pk}/'),'원본소개')


class StorageTests(SimpleTestCase):
    def test_valid_service_account_key_constructs_client_without_network(self):
        import json
        from unittest.mock import mock_open
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.hazmat.primitives import serialization
        from google.auth.transport.requests import AuthorizedSession
        key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
        info={'type':'service_account','client_email':'test@example.iam.gserviceaccount.com',
              'private_key':key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()).decode(),
              'token_uri':'https://oauth2.googleapis.com/token'}
        with override_settings(GOOGLE_DRIVE_CREDENTIALS_FILE='example.json'),patch('builtins.open',mock_open(read_data=json.dumps(info))):
            client=DriveClient();self.assertEqual(client.session.credentials.service_account_email,info['client_email']);client.session.close()
    def test_missing_secret_file_has_actionable_message_without_secret(self):
        from google.auth.transport.requests import AuthorizedSession
        with patch('builtins.open',side_effect=FileNotFoundError):
            with self.assertRaises(RepositoryError) as error:DriveClient()
        self.assertIn('Secret Files',str(error.exception))
    def test_invalid_json_and_wrong_credential_kind_are_safe_errors(self):
        from unittest.mock import mock_open
        from google.auth.transport.requests import AuthorizedSession
        for text in ['PRIVATE KEY broken JSON','{"type":"authorized_user","refresh_token":"SECRET"}']:
            with patch('builtins.open',mock_open(read_data=text)):
                with self.assertRaises(RepositoryError) as error:DriveClient()
            self.assertNotIn('PRIVATE KEY',str(error.exception));self.assertNotIn('SECRET',str(error.exception))
    def test_legacy_local_files_readable_even_when_drive_configured(self):
        from django.conf import settings
        temp_root=Path(settings.BASE_DIR)/'.tmp';temp_root.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=temp_root) as directory, override_settings(MEDIA_ROOT=directory,GOOGLE_DRIVE_CREDENTIALS_FILE='unused',GOOGLE_DRIVE_SHARED_DRIVE_ID='drive'):
            p=Path(directory)/'old.pdf';p.write_bytes(b'legacy')
            with WorkhubStorage().open('old.pdf') as f:self.assertEqual(f.read(),b'legacy')
    def test_private_storage_has_no_public_url_or_deletion_hook(self):
        with self.assertRaises(NotImplementedError):WorkhubStorage().url('drive/x')
        with self.assertRaises(NotImplementedError):WorkhubStorage().delete('drive/x')
    def drive_client(self):
        c=DriveClient.__new__(DriveClient);c.drive_id='drive';c.session=Mock();return c
    def test_upstream_errors_never_echo_response_or_secret(self):
        c=self.drive_client();c.session.request.side_effect=RuntimeError('PRIVATE KEY or session URL')
        with self.assertRaises(RepositoryError) as error:c.request('GET','drive/v3/files/x')
        self.assertNotIn('PRIVATE',str(error.exception));self.assertNotIn('URL',str(error.exception))
    def test_wrong_drive_and_trashed_files_rejected_before_download(self):
        for meta in [{'driveId':'foreign'},{'driveId':'drive','trashed':True},{'driveId':'drive','appProperties':{}}]:
            c=self.drive_client();response=Mock();response.__enter__=Mock(return_value=response);response.__exit__=Mock();response.json.return_value=meta;c.request=Mock(return_value=response)
            with self.assertRaises(RepositoryError):c.open('file')
            self.assertEqual(c.request.call_count,1)
    def test_resumable_upload_rejects_untrusted_session_host(self):
        c=self.drive_client();response=Mock();response.__enter__=Mock(return_value=response);response.__exit__=Mock();response.headers={'Location':'https://evil.example/upload'};c.request=Mock(return_value=response)
        f=SimpleUploadedFile('x.pdf',b'data')
        with self.assertRaises(RepositoryError):c.upload('x.pdf',f)
        self.assertEqual(c.request.call_count,1)

    def test_connection_checks_shared_drive_resource_and_upload_permission(self):
        for writable in [True,False]:
            c=self.drive_client();responses=[]
            for data in [{'id':'drive','name':'Workhub 업무자료'},
                {'name':'Drive','mimeType':'application/vnd.google-apps.folder','capabilities':{'canAddChildren':writable}}]:
                r=Mock();r.__enter__=Mock(return_value=r);r.__exit__=Mock();r.json.return_value=data;responses.append(r)
            c.request=Mock(side_effect=responses)
            if writable:self.assertEqual(c.check(),'Workhub 업무자료')
            else:
                with self.assertRaises(RepositoryError):c.check()
            self.assertIn('/drives/',c.request.call_args_list[0].args[1])

    def test_upload_checks_size_and_checksum_before_accepting_file_id(self):
        import hashlib
        for valid in [True,False]:
            c=self.drive_client()
            init=Mock();init.__enter__=Mock(return_value=init);init.__exit__=Mock();init.headers={'Location':'https://www.googleapis.com/upload/drive/v3/files?upload_id=example'}
            done=Mock();done.__enter__=Mock(return_value=done);done.__exit__=Mock()
            done.json.return_value={'id':'file123','size':'4','md5Checksum':hashlib.md5(b'data').hexdigest() if valid else 'bad'}
            c.request=Mock(side_effect=[init,done])
            if valid:self.assertEqual(c.upload('x.pdf',SimpleUploadedFile('x.pdf',b'data')),'drive/file123')
            else:
                with self.assertRaises(RepositoryError):c.upload('x.pdf',SimpleUploadedFile('x.pdf',b'data'))
