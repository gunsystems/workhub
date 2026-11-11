from datetime import timedelta
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied,ValidationError
from django.test import TestCase,override_settings
from django.utils import timezone
from accounts.models import AccessProfile
from .models import Department,Employee,Task,QuickRequest,QuickDelivery,HiworksLink
from .quick import create,change,visible,deliver_pending


@override_settings(WORKHUB_REQUIRE_HIWORKS=False,WORKHUB_SCHEDULER_ENABLED=False,
    PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'],EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class QuickTests(TestCase):
    def test_requester_can_finish_from_comments_without_inventing_reply(self):
        item=self.new();self.act(item,self.worker,'comment','확인 결과 이상 없습니다.')
        with self.assertRaises(PermissionDenied):self.act(item,self.sender,'confirm')
        self.act(item,self.worker,'accept');self.act(item,self.sender,'confirm');item.refresh_from_db()
        self.assertEqual(item.state,'done');self.assertIsNotNone(item.completed_at);self.assertIsNone(item.replied_at)
        self.assertIn('의견·자료',item.events.first().body)
    def test_dashboard_menu_and_current_stage(self):
        item=self.new();self.client.force_login(self.sender)
        self.assertContains(self.client.get('/dashboard/?scope=mine'),'고객 확인')
        response=self.client.get(f'/work/quick/{item.pk}/');self.assertContains(response,'현재 · 확인 대기')
        self.assertContains(response,'업무 완료 · 요청자 확인');self.assertContains(response,'/work/quick/')
        self.act(item,self.worker,'accept');self.assertContains(self.client.get(f'/work/quick/{item.pk}/'),'현재 · 처리 중')
        self.client.force_login(self.other);self.assertNotContains(self.client.get('/dashboard/?scope=mine'),'고객 확인')
    def setUp(self):
        U=get_user_model();self.admin=U.objects.create_superuser('operator')
        self.team=Department.objects.create(name='영업');self.otherteam=Department.objects.create(name='기술')
        self.users=[];self.staff=[]
        for name,team in [('sender',self.team),('worker',self.otherteam),('other',self.team)]:
            user=U.objects.create_user(name,email=name+'@gunsystems.co.kr')
            AccessProfile.objects.create(user=user,email=user.email,verified_at=timezone.now(),require_mfa=False)
            self.users.append(user);self.staff.append(Employee.objects.create(user=user,name=name,department=team))
        self.sender,self.worker,self.other=self.users
    def new(self,channels=False):
        form,item=create(self.sender,{'title':'고객 확인','body':'고객에게 진행 현황을 확인해 주세요.','assignee':self.staff[1].pk,'send_email':channels,'send_hiworks':channels})
        self.assertTrue(form.is_valid());return item
    def act(self,item,user,action,body=''):
        item.refresh_from_db();return change(item.pk,user,action,body,item.revision)
    def test_date_free_and_cross_team_creation(self):
        item=self.new();self.assertIsNotNone(item.created_at);self.assertEqual(Task.objects.count(),0)
        self.assertEqual(item.state,'pending');self.assertEqual(item.events.count(),1)
        self.assertTrue(QuickDelivery.objects.filter(user=self.worker,channel='app').exists())
    def test_acknowledgement_required_and_requester_confirms(self):
        item=self.new()
        with self.assertRaises(PermissionDenied):self.act(item,self.worker,'reply','결과')
        self.act(item,self.worker,'accept');item.refresh_from_db();accepted=item.accepted_at
        self.assertIsNotNone(accepted)
        with self.assertRaises(ValidationError):self.act(item,self.worker,'reply')
        self.act(item,self.worker,'reply','진행 확인했습니다.')
        with self.assertRaises(PermissionDenied):self.act(item,self.worker,'confirm')
        self.act(item,self.sender,'confirm');item.refresh_from_db()
        self.assertEqual(item.state,'done');self.assertEqual(item.accepted_at,accepted)
        self.assertLessEqual(item.created_at,item.accepted_at);self.assertLessEqual(item.replied_at,item.completed_at)
    def test_return_keeps_original_ack_and_all_replies(self):
        item=self.new();self.act(item,self.worker,'accept');item.refresh_from_db();accepted=item.accepted_at
        self.act(item,self.worker,'reply','1차')
        with self.assertRaises(ValidationError):self.act(item,self.sender,'return')
        self.act(item,self.sender,'return','추가 확인');self.act(item,self.worker,'reply','2차')
        item.refresh_from_db();self.assertEqual(item.accepted_at,accepted);self.assertEqual(item.events.filter(action='결과 회신').count(),2)
    def test_comments_do_not_acknowledge(self):
        item=self.new();self.act(item,self.worker,'comment','어느 고객인가요?');item.refresh_from_db()
        self.assertEqual(item.state,'pending');self.assertIsNone(item.accepted_at)
    def test_duplicate_and_stale_submissions_do_not_create_events(self):
        item=self.new();self.act(item,self.worker,'accept')
        with self.assertRaises(ValidationError):change(item.pk,self.worker,'accept','',0)
        with self.assertRaises(PermissionDenied):self.act(item,self.worker,'accept')
        self.assertEqual(item.events.count(),2)
    def test_visibility_and_observers_cannot_act(self):
        item=self.new();self.assertFalse(visible(self.other).filter(pk=item.pk).exists())
        self.assertTrue(visible(self.admin).filter(pk=item.pk).exists())
        for user in [self.other,self.admin]:
            with self.assertRaises(PermissionDenied):self.act(item,user,'accept')
        self.staff[2].department=self.otherteam;self.staff[2].position='leader';self.staff[2].save()
        self.assertTrue(visible(self.other).filter(pk=item.pk).exists())
        with self.assertRaises(PermissionDenied):self.act(item,self.other,'confirm')
    def test_cancellation_requires_owner_and_reason_and_is_terminal(self):
        item=self.new()
        with self.assertRaises(PermissionDenied):self.act(item,self.worker,'cancel','사유')
        with self.assertRaises(ValidationError):self.act(item,self.sender,'cancel')
        self.act(item,self.sender,'cancel','더 이상 필요 없음');item.refresh_from_db()
        self.assertIsNotNone(item.cancelled_at)
        with self.assertRaises(ValidationError):self.act(item,self.worker,'accept')
    def test_inactive_or_unverified_recipients_rejected(self):
        self.worker.workhub_access.verified_at=None;self.worker.workhub_access.save()
        form,item=create(self.sender,{'title':'요청','body':'내용','assignee':self.staff[1].pk})
        self.assertIsNone(item);self.assertIn('assignee',form.errors)
    @patch('accounts.services.deliver')
    def test_delivery_deduplication_and_no_ambiguous_retry(self,send):
        item=self.new(True);send.side_effect=RuntimeError('provider timeout')
        deliver_pending();deliver_pending()
        self.assertEqual(send.call_count,1)
        self.assertEqual(QuickDelivery.objects.get(channel='email').state,'unknown')
        self.assertEqual(QuickDelivery.objects.get(channel='hiworks').state,'skipped')
    @patch('accounts.services.deliver')
    def test_delivery_rechecks_access_and_email(self,send):
        self.new(True);self.worker.email='changed@gunsystems.co.kr';self.worker.save();deliver_pending()
        send.assert_not_called();self.assertFalse(QuickDelivery.objects.filter(state='sent').exclude(channel='app').exists())
    @patch('tasks.hiworks.api')
    def test_hiworks_respects_opt_out(self,api):
        item=self.new(True)
        HiworksLink.objects.create(employee=self.staff[1],email=self.worker.email,user_id='worker',remote_id='1',notifications_enabled=False)
        with patch('accounts.services.deliver'):deliver_pending()
        api.assert_not_called()
    def test_pages_and_inapp_notification_access(self):
        item=self.new();self.client.force_login(self.worker)
        self.assertContains(self.client.get(f'/work/quick/{item.pk}/'),'요청 확인')
        self.assertIsNotNone(QuickDelivery.objects.get(channel='app').read_at)
        self.assertEqual(self.client.get('/work/quick/').status_code,200)
        self.assertContains(self.client.get('/work/quick/new/'),'간단 업무 전달')
        self.assertEqual(self.client.get('/work/notifications/').status_code,200)
        self.client.force_login(self.other);self.assertEqual(self.client.get(f'/work/quick/{item.pk}/').status_code,404)
    def test_account_deletion_preserves_history_and_blocks_employee_delete(self):
        from accounts.lifecycle import business_counts
        item=self.new();self.assertEqual(business_counts(self.staff[1])['담당 간단 업무'],1)
        self.sender.delete();item.refresh_from_db();self.assertIsNone(item.requester_id)
        self.assertTrue(item.requester_name);self.assertTrue(item.events.get().actor_name)
    @override_settings(GOOGLE_DRIVE_CREDENTIALS_FILE='/etc/secrets/example.json',GOOGLE_DRIVE_SHARED_DRIVE_ID='shared-drive')
    @patch('tasks.drive_storage.DriveClient.upload',return_value='drive/quick-reference')
    def test_initial_file_and_article_link_share_object_acl(self,upload):
        from django.core.files.uploadedfile import SimpleUploadedFile
        from .models import WorkDocument
        with patch('tasks.drive_storage.DriveClient.__init__',return_value=None):
            form,item=create(self.sender,{'title':'기사 검토','body':'문서와 기사를 검토해 주세요.','assignee':self.staff[1].pk,'link':'https://example.com/article'},
                {'attachment':SimpleUploadedFile('검토자료.pdf',b'%PDF-example')})
        self.assertIsNotNone(item);upload.assert_called_once();self.assertEqual(item.documents.count(),2)
        doc=WorkDocument.objects.get(quick_request=item,link='');self.assertEqual(doc.file.name,'drive/quick-reference')
        self.client.force_login(self.worker);self.assertContains(self.client.get(f'/work/quick/{item.pk}/'),'검토자료.pdf')
        self.client.force_login(self.other)
        with patch('tasks.drive_storage.WorkhubStorage._open') as read:
            self.assertEqual(self.client.get(f'/work/documents/{doc.pk}/download/').status_code,404);read.assert_not_called()
    @override_settings(GOOGLE_DRIVE_CREDENTIALS_FILE='/etc/secrets/example.json',GOOGLE_DRIVE_SHARED_DRIVE_ID='shared-drive')
    def test_upload_error_rolls_back_request_and_notifications(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        from .drive_storage import RepositoryError
        self.client.force_login(self.sender)
        with patch('tasks.drive_storage.DriveClient.__init__',return_value=None),patch('tasks.drive_storage.DriveClient.upload',side_effect=RepositoryError('저장소 연결 오류')):
            response=self.client.post('/work/quick/new/',{'title':'검토','body':'내용','assignee':self.staff[1].pk,'attachment':SimpleUploadedFile('검토.pdf',b'%PDF')})
        self.assertContains(response,'저장소 연결 오류');self.assertFalse(QuickRequest.objects.exists());self.assertFalse(QuickDelivery.objects.exists())
    def test_article_validation_and_result_materials(self):
        for link in ['javascript:alert(1)','ftp://example.com/article']:
            form,item=create(self.sender,{'title':'검토','body':'내용','assignee':self.staff[1].pk,'link':link})
            self.assertIsNone(item);self.assertIn('link',form.errors)
        item=self.new();self.client.force_login(self.worker)
        response=self.client.post(f'/work/documents/quick/{item.pk}/add/',{'name':'검토 결과','kind':'result','link':'https://example.com/result'})
        self.assertEqual(response.status_code,302);self.assertEqual(item.documents.get().kind,'result')
        self.act(item,self.worker,'accept');self.act(item,self.worker,'reply','결과 자료를 확인하세요.');self.act(item,self.sender,'confirm')
        self.assertEqual(self.client.get(f'/work/documents/quick/{item.pk}/add/').status_code,403)
