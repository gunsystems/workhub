import io
import tempfile
from datetime import timedelta
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.core import mail
from django.http import Http404
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings
from django.utils import timezone
from .models import Department,Employee,OffboardingCase,OffboardingItem,OffboardingEvent,OffboardingEvidence,OffboardingNotice
from . import offboarding as o


@override_settings(WORKHUB_REQUIRE_HIWORKS=False,WORKHUB_SCHEDULER_ENABLED=False)
class OffboardingTests(TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.storage=override_settings(MEDIA_ROOT=self.tmp.name,STORAGES={'default':{'BACKEND':'django.core.files.storage.FileSystemStorage'},'staticfiles':{'BACKEND':'django.contrib.staticfiles.storage.StaticFilesStorage'}})
        self.storage.enable();self.addCleanup(self.storage.disable);self.addCleanup(self.tmp.cleanup)
        U=get_user_model();self.admin=U.objects.create_superuser('off_admin',email='admin@example.com')
        self.worker=U.objects.create_user('off_worker',email='worker@example.com')
        self.other=U.objects.create_user('off_other',email='other@example.com')
        self.target=U.objects.create_user('off_target',email='target@example.com')
        d=Department.objects.create(name='후속조치팀')
        for u in [self.worker,self.other,self.target]:Employee.objects.create(user=u,name=u.username,department=d)
        self.case=o.create_case(self.admin,self.target.employee,timezone.localdate()+timedelta(days=3))
        self.item=self.case.items.first();self.item.assignee=self.worker;self.item.save()
    def change(self,action,user=None,**kw):
        self.item.refresh_from_db()
        return o.change_item(self.item.pk,user or self.worker,action,self.item.revision,**kw)
    def submit(self):
        return self.change('submit',data={'processed_at':timezone.now()-timedelta(minutes=3),'body':'단말기 반영 확인','evidence':SimpleUploadedFile('증빙.png',self.png(),content_type='image/png')})
    def png(self):
        from PIL import Image
        out=io.BytesIO();Image.new('RGB',(20,20),'green').save(out,format='PNG');return out.getvalue()
    def test_creation_manual_only_and_duplicate(self):
        self.assertEqual(self.case.items.count(),6);self.target.refresh_from_db();self.assertTrue(self.target.is_active)
        self.assertTrue(self.target.employee.is_active)
        with self.assertRaises(IntegrityError),transaction.atomic():o.create_case(self.admin,self.target.employee,self.case.departure_date)
        with self.assertRaises(PermissionDenied):o.create_case(self.worker,self.target.employee,self.case.departure_date)
    def test_access_is_per_item_not_case_or_department(self):
        second=self.case.items.last()
        self.client.force_login(self.worker)
        for url in ['/work/offboarding/','/work/offboarding/new/',f'/work/offboarding/cases/{self.case.pk}/']:
            self.assertEqual(self.client.get(url).status_code,403)
        self.assertEqual(self.client.get(f'/work/offboarding/items/{self.item.pk}/').status_code,200)
        self.assertEqual(self.client.get(f'/work/offboarding/items/{second.pk}/').status_code,404)
        self.client.force_login(self.target);self.assertEqual(self.client.get(f'/work/offboarding/items/{self.item.pk}/').status_code,404)
    def test_cannot_approve_as_assignee_and_review_requires_submission(self):
        with self.assertRaises(PermissionDenied):self.change('approve',self.admin)
        self.submit()
        with self.assertRaises(PermissionDenied):self.change('approve')
        self.change('approve',self.admin)
        self.item.refresh_from_db();self.assertEqual(self.item.state,'approved')
        self.assertEqual(self.item.events.filter(action='확인 완료').first().snapshot['제출 기록'],self.item.events.get(action='처리 결과 제출').pk)
    def test_returns_preserve_old_evidence_and_actual_vs_reported_time(self):
        self.submit();first=self.item.events.get(action='처리 결과 제출')
        with self.assertRaises(ValidationError):self.change('return',self.admin)
        self.change('return',self.admin,body='단말기 반영 화면 추가');self.submit()
        self.assertEqual(self.item.events.filter(action='처리 결과 제출').count(),2)
        self.assertEqual(first.evidence.count(),1);self.assertLess(first.processed_at,first.created_at)
    def test_stale_actions_rejected(self):
        self.change('start')
        with self.assertRaises(ValidationError):o.change_item(self.item.pk,self.worker,'start',0)
        self.assertEqual(self.item.events.filter(action='처리 시작').count(),1)
    def test_submission_validation_storage_size_format_future(self):
        data={'processed_at':timezone.localtime().strftime('%Y-%m-%dT%H:%M'),'body':'처리'}
        with patch('tasks.offboarding.configured',return_value=True):
            self.assertFalse(o.SubmitForm(data).is_valid())
            self.assertFalse(o.SubmitForm(data,{'evidence':SimpleUploadedFile('x.html',b'<script>')}).is_valid())
            self.assertTrue(o.SubmitForm(data,{'evidence':SimpleUploadedFile('x.pdf',b'%PDF-1.4')}).is_valid())
            self.assertFalse(o.SubmitForm({**data,'processed_at':timezone.now()+timedelta(days=1)},{'evidence':SimpleUploadedFile('x.pdf',b'%PDF')}).is_valid())
        with patch('tasks.offboarding.configured',return_value=False):self.assertFalse(o.SubmitForm(data,{'evidence':SimpleUploadedFile('x.png',self.png())}).is_valid())
    def test_evidence_download_preview_acl_and_no_public_url(self):
        self.submit();f=OffboardingEvidence.objects.get();self.client.force_login(self.worker)
        response=self.client.get(f'/work/offboarding/evidence/{f.pk}/');self.assertEqual(response.status_code,200);self.assertEqual(b''.join(response.streaming_content),self.png())
        preview=self.client.get(f'/work/offboarding/evidence/{f.pk}/preview/');self.assertEqual(preview.status_code,200);self.assertEqual(preview['Content-Type'],'image/jpeg')
        self.assertIn('no-store',preview['Cache-Control'])
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(f'/work/offboarding/evidence/{f.pk}/').status_code,404)
        self.assertEqual(self.client.get(f'/work/offboarding/evidence/{f.pk}/preview/').status_code,404)
    def test_reassignment_revokes_old_access_and_excludes_departing_user(self):
        self.client.force_login(self.admin)
        data={'title':self.item.title,'instruction':'확인','assignee':self.other.pk,'due_at':(timezone.now()+timedelta(days=1)).isoformat(),'revision':0}
        self.assertEqual(self.client.post(f'/work/offboarding/cases/{self.case.pk}/items/{self.item.pk}/edit/',data).status_code,302)
        self.client.force_login(self.worker);self.assertEqual(self.client.get(f'/work/offboarding/items/{self.item.pk}/').status_code,404)
        form=o.ItemForm(case=self.case);self.assertFalse(form.fields['assignee'].queryset.filter(pk=self.target.pk).exists())
    def test_finish_requires_every_item_confirmed_and_cancel_blocks_action(self):
        self.client.force_login(self.admin)
        response=self.client.post(f'/work/offboarding/cases/{self.case.pk}/',{'action':'finish','revision':0});self.assertEqual(response.status_code,200)
        self.case.refresh_from_db();self.assertEqual(self.case.state,'active')
        self.case.items.update(state='approved')
        self.client.post(f'/work/offboarding/cases/{self.case.pk}/',{'action':'finish','revision':0})
        self.case.refresh_from_db();self.assertEqual(self.case.state,'done');self.assertIsNotNone(self.case.completed_at)
        with self.assertRaises(ValidationError):self.change('start')
    def test_cancel_and_date_change_audit_do_not_touch_accounts(self):
        self.client.force_login(self.admin);new=self.case.departure_date+timedelta(days=1);old=self.case.departure_date
        self.client.post(f'/work/offboarding/cases/{self.case.pk}/',{'action':'date','revision':0,'departure_date':new.isoformat(),'reason':'일정 변경'})
        self.case.refresh_from_db();self.assertEqual(self.case.departure_date,new)
        event=self.case.events.get(action='퇴사일 변경');self.assertEqual(event.snapshot['이전'],old.isoformat())
        self.client.post(f'/work/offboarding/cases/{self.case.pk}/',{'action':'cancel','revision':1,'reason':'퇴사 취소'})
        self.case.refresh_from_db();self.assertEqual(self.case.state,'cancelled');self.target.refresh_from_db();self.assertTrue(self.target.is_active)
        with self.assertRaises(ValidationError):self.change('start')
    def test_reminders_idempotent_and_mail_privacy_and_completion_suppression(self):
        self.item.due_at=timezone.now()-timedelta(hours=1);self.item.save()
        o.queue_reminders();n=OffboardingNotice.objects.count();o.queue_reminders();self.assertEqual(OffboardingNotice.objects.count(),n)
        o.deliver_notices();sent=len(mail.outbox);o.deliver_notices();self.assertEqual(len(mail.outbox),sent)
        self.assertGreater(sent,0);self.assertNotIn('off_target',mail.outbox[0].body)
        self.case.state='cancelled';self.case.save();OffboardingNotice.objects.all().delete();o.queue_reminders();self.assertFalse(OffboardingNotice.objects.exists())
    def test_ui_navigation_and_notification_entry(self):
        self.client.force_login(self.admin)
        for url in ['/work/offboarding/','/work/offboarding/new/',f'/work/offboarding/cases/{self.case.pk}/',f'/work/offboarding/cases/{self.case.pk}/items/{self.item.pk}/edit/']:
            self.assertEqual(self.client.get(url).status_code,200,url)
        self.client.force_login(self.worker);o.notice(self.item,[self.worker],'test','처리 요청')
        self.assertContains(self.client.get('/work/notifications/'),'배정 항목 확인')
        self.assertContains(self.client.get('/work/offboarding/assigned/'),'배정된 후속 조치')
        self.assertNotContains(self.client.get('/work/'),'href="/work/offboarding/"')
    def test_csrf_post_required_and_get_does_not_change(self):
        from django.test import Client
        client=Client(enforce_csrf_checks=True);client.force_login(self.admin)
        self.assertEqual(client.post(f'/work/offboarding/cases/{self.case.pk}/',{'action':'cancel','revision':0,'reason':'취소'}).status_code,403)
        self.client.force_login(self.worker);before=self.item.events.count()
        self.client.get(f'/work/offboarding/items/{self.item.pk}/?action=start');self.assertEqual(self.item.events.count(),before)
    def test_waiver_is_admin_only_with_reason_and_audited(self):
        with self.assertRaises(PermissionDenied):self.change('waive',body='해당 없음')
        with self.assertRaises(ValidationError):self.change('waive',self.admin)
        self.change('waive',self.admin,body='캡스 계정이 없는 직원')
        self.item.refresh_from_db();self.assertEqual(self.item.state,'approved')
        self.assertEqual(self.item.events.get(action='해당 없음 · 관리자 확인').body,'캡스 계정이 없는 직원')
    def test_upload_failure_keeps_state_and_history_unchanged(self):
        before=self.item.events.count()
        from .drive_storage import RepositoryError
        with patch('django.core.files.storage.FileSystemStorage._save',side_effect=RepositoryError('연결 오류')):
            with self.assertRaises(RepositoryError):self.submit()
        self.item.refresh_from_db();self.assertEqual(self.item.state,'pending');self.assertEqual(self.item.events.count(),before)
    def test_stale_finish_after_an_item_change_is_rejected(self):
        self.change('start');self.case.items.update(state='approved');self.client.force_login(self.admin)
        self.client.post(f'/work/offboarding/cases/{self.case.pk}/',{'action':'finish','revision':0})
        self.case.refresh_from_db();self.assertEqual(self.case.state,'active')
    def test_alerts_scoped_and_account_deletion_reports_retained_records(self):
        from .alerts import action_alerts
        from accounts.lifecycle import business_counts,change_access
        self.item.due_at=timezone.now()-timedelta(days=1);self.item.save()
        self.assertTrue(any(a['key']==f'offboarding:{self.item.pk}' for a in action_alerts(self.worker)))
        self.assertFalse(any(a['key'].startswith('offboarding:') for a in action_alerts(self.other)))
        self.assertEqual(business_counts(self.target.employee)['퇴사 후속 조치 기록'],1)
        with self.assertRaises(ValidationError):change_access(self.admin,self.worker.employee.pk,'delete_access','삭제',self.worker.email)

    def test_cancel_ack_moves_only_own_item_to_history_and_is_idempotent(self):
        self.case.state='cancelled';self.case.save()
        self.client.force_login(self.worker)
        response=self.client.get('/work/offboarding/assigned/')
        self.assertEqual(response.context['active_count'],0)
        self.assertEqual(response.context['cancelled_count'],1)
        self.assertEqual(len(response.context['items']),0)
        response=self.client.get('/work/offboarding/assigned/?box=cancelled')
        self.assertContains(response,'취소 확인 필요');self.assertNotContains(response,'처리 대기')
        self.assertEqual(len(response.context['items']),1)
        url=f'/work/offboarding/items/{self.item.pk}/'
        self.assertContains(self.client.get(url),'취소 확인')
        for _ in range(2):
            self.assertEqual(self.client.post(url,{'action':'ack_cancel','revision':0}).status_code,302)
        self.assertEqual(self.item.events.filter(action='퇴사 취소 확인').count(),1)
        self.item.refresh_from_db();self.assertEqual(self.item.state,'pending')
        self.assertFalse(OffboardingEvidence.objects.exists())
        response=self.client.get('/work/offboarding/assigned/?box=history')
        self.assertEqual(len(response.context['items']),1);self.assertContains(response,'취소 확인 완료')
        self.assertEqual(response.context['cancelled_count'],0)

    def test_cancel_ack_denies_other_admin_and_active_case(self):
        with self.assertRaises(PermissionDenied):self.change('ack_cancel')
        self.case.state='cancelled';self.case.save()
        with self.assertRaises(PermissionDenied):self.change('ack_cancel',self.admin)
        with self.assertRaises(Http404):self.change('ack_cancel',self.other)
        with self.assertRaises(ValidationError):o.change_item(self.item.pk,self.worker,'ack_cancel',999)
        self.assertFalse(self.item.events.filter(action='퇴사 취소 확인').exists())
