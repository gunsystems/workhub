import hashlib,secrets
from unittest.mock import patch
from types import SimpleNamespace
from django.test import TestCase,Client,override_settings
from . import test_unified_work as fixtures
from .models import Task,TaskTrash,TaskEvent,CalendarSubscription,CalendarExport,SystemAudit
from .google_calendar import sync_export
from .permissions import visible_tasks


@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'],WORKHUB_REQUIRE_HIWORKS=False,WORKHUB_SCHEDULER_ENABLED=False)
class TaskTrashTests(TestCase):
    setUp=fixtures.UnifiedWorkTests.setUp

    def remove(self,**extra):
        return self.client.post(f'/work/tasks/{self.task.pk}/delete/',{'reason':'테스트 일정 정리','revision':self.task.revision,**extra})

    def test_root_removal_hides_task_preserves_history_and_can_restore(self):
        event=TaskEvent.objects.create(task=self.task,author=self.worker,kind='comment',body='보존할 의견')
        self.assertEqual(self.client.get(f'/work/tasks/{self.task.pk}/delete/').status_code,200)
        self.assertEqual(TaskTrash.objects.count(),0)
        self.assertEqual(self.remove().status_code,302)
        self.assertFalse(Task.objects.filter(pk=self.task.pk).exists())
        self.assertTrue(Task.all_objects.get(pk=self.task.pk).is_deleted)
        self.assertTrue(TaskEvent.objects.filter(pk=event.pk).exists())
        self.assertFalse(visible_tasks(self.worker).filter(pk=self.task.pk).exists())
        self.assertEqual(self.client.get(f'/tasks/{self.task.pk}/').status_code,404)
        self.assertContains(self.client.get('/work/trash/'),'테스트 일정 정리')
        record=TaskTrash.objects.get();self.assertEqual(record.previous_status,'todo')
        self.assertTrue(SystemAudit.objects.filter(action='delete',target_type='Task',target_id=str(self.task.pk),actor_id_snapshot=self.root.pk).exists())
        hidden=Task.all_objects.get(pk=self.task.pk)
        self.assertEqual(self.client.post('/work/trash/',{'task_id':hidden.pk,'revision':hidden.revision}).status_code,302)
        restored=Task.objects.get(pk=hidden.pk);self.assertEqual(restored.status,'todo')
        record.refresh_from_db();self.assertIsNotNone(record.restored_at)
        self.assertTrue(SystemAudit.objects.filter(action='restore',target_id=str(hidden.pk)).exists())

    def test_staff_admin_can_delete_but_executive_leader_and_worker_cannot(self):
        for user in [self.worker,self.lead,self.ceo]:
            self.client.force_login(user)
            self.assertEqual(self.client.get('/work/trash/').status_code,403)
            self.assertEqual(self.remove().status_code,403)
        self.lead.is_staff=True;self.lead.save(update_fields=['is_staff'])
        from accounts.models import AccessProfile
        from django_otp.plugins.otp_totp.models import TOTPDevice
        from django.utils import timezone
        AccessProfile.objects.create(user=self.lead,email='lead@gunsystems.co.kr',verified_at=timezone.now())
        TOTPDevice.objects.create(user=self.lead,confirmed=True)
        self.client.force_login(self.lead)
        session=self.client.session;session['otp_device_id']=TOTPDevice.objects.get(user=self.lead).persistent_id;session.save()
        self.assertEqual(self.remove().status_code,302)
        self.assertTrue(Task.all_objects.get(pk=self.task.pk).is_deleted)

    def test_reason_revision_and_csrf_are_required(self):
        self.assertContains(self.remove(reason='  '),'필수')
        self.assertContains(self.remove(revision=999),'업무가 변경되었습니다')
        self.assertFalse(TaskTrash.objects.exists())
        secure=Client(enforce_csrf_checks=True);secure.force_login(self.root)
        self.assertEqual(secure.post(f'/work/tasks/{self.task.pk}/delete/',{'reason':'test','revision':0}).status_code,403)
        self.assertEqual(self.client.post('/work/trash/',{'task_id':'bad'}).status_code,404)

    def test_feed_omits_deleted_uid_and_restores_same_uid(self):
        token=secrets.token_urlsafe(32)
        CalendarSubscription.objects.create(owner=self.worker,token_digest=hashlib.sha256(token.encode()).hexdigest())
        url='/work/calendar/subscription/'+token+'.ics'
        uid=f'UID:workhub-task-{self.task.pk}@gunsystems.co.kr'
        self.assertContains(Client().get(url),uid)
        self.remove();self.assertNotContains(Client().get(url),uid)
        task=Task.all_objects.get(pk=self.task.pk)
        self.client.post('/work/trash/',{'task_id':task.pk,'revision':task.revision})
        self.assertContains(Client().get(url),uid)

    @patch('tasks.google_calendar.request_json')
    def test_google_export_deletes_existing_event_when_task_trashed(self,request_json):
        export=CalendarExport.objects.create(user=self.worker,task=self.task,detail='title',event_id='existing',calendar_id='cal')
        self.remove();export=CalendarExport.objects.select_related('task').get(pk=export.pk)
        sync_export(SimpleNamespace(calendar_id='cal',user_id=self.worker.pk),export,'test-token')
        self.assertEqual(request_json.call_args.args[1],'DELETE')
        export.refresh_from_db();self.assertEqual(export.event_id,'')
        task=Task.all_objects.get(pk=self.task.pk)
        self.client.post('/work/trash/',{'task_id':task.pk,'revision':task.revision})
        export=CalendarExport.objects.select_related('task').get(pk=export.pk)
        request_json.reset_mock()
        sync_export(SimpleNamespace(calendar_id='cal',user_id=self.worker.pk),export,'test-token')
        original='a'+hashlib.sha256(f'{self.worker.pk}:task:{task.pk}'.encode()).hexdigest()
        self.assertNotEqual(export.event_id,original)
        self.assertEqual(request_json.call_args.args[1],'PUT')

    def test_removal_does_not_change_other_tasks_or_allow_stale_restore(self):
        other=Task.objects.create(title='실제 업무',assignee=self.emp,start_date=self.day,due_date=self.day)
        self.remove();task=Task.all_objects.get(pk=self.task.pk)
        self.client.post('/work/trash/',{'task_id':task.pk,'revision':999})
        self.assertTrue(Task.all_objects.get(pk=task.pk).is_deleted)
        self.assertTrue(Task.objects.filter(pk=other.pk).exists())
