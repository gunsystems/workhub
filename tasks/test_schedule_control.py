from datetime import datetime,timedelta
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.core import mail
from django.test import TestCase,override_settings
from django.utils import timezone
from accounts.models import AccessProfile
from .models import (Task,TaskParticipation,Department,Employee,ApprovalStep,
    ScheduleRegistration,ScheduleOutcome,ScheduleExpectation,DeadlinePolicy,DeadlineDelivery,WorkNotification)
from .schedule_control import queue_deadlines,deliver_deadlines,SEOUL
from .workflow import record,snapshot


@override_settings(WORKHUB_REQUIRE_HIWORKS=False,WORKHUB_SCHEDULER_ENABLED=False,
    PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'],EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class ScheduleControlTests(TestCase):
    def setUp(self):
        self.now=datetime(2026,11,10,10,tzinfo=SEOUL);self.day=self.now.date()
        U=get_user_model();self.admin=U.objects.create_superuser('schedule-admin')
        team=Department.objects.create(name='일정팀');other=Department.objects.create(name='별도팀')
        self.worker=U.objects.create_user('schedule-worker',email='schedule-worker@gunsystems.co.kr')
        self.lead=U.objects.create_user('schedule-lead',email='schedule-lead@gunsystems.co.kr')
        self.outside=U.objects.create_user('schedule-outside',email='schedule-outside@gunsystems.co.kr')
        for u in [self.worker,self.lead,self.outside]:AccessProfile.objects.create(user=u,email=u.email,verified_at=self.now,require_mfa=False)
        self.boss=Employee.objects.create(name='팀장',user=self.lead,department=team,role='manager',position='leader')
        self.emp=Employee.objects.create(name='직원',user=self.worker,department=team,supervisor=self.boss)
        self.foreign=Employee.objects.create(name='다른팀직원',user=self.outside,department=other)
        self.task=Task.objects.create(title='교육 일정',creator=self.admin,assignee=self.emp,workflow_version=2,management_mode='event',start_date=self.day-timedelta(days=1),due_date=self.day,status='doing')
        TaskParticipation.objects.create(task=self.task,employee=self.emp)
        self.policy=DeadlinePolicy.objects.create(pk=1,enabled=True,activated_at=self.now,weekdays_only=False)
        self.client.force_login(self.worker)
    def test_default_policy_off_and_get_pages_do_not_create_deliveries(self):
        self.policy.delete();self.assertEqual(queue_deadlines(self.now),0)
        for url in ['/work/schedule-control/',f'/work/schedule-control/task/{self.task.pk}/']:
            self.assertEqual(self.client.get(url).status_code,200)
        self.assertFalse(DeadlineDelivery.objects.exists());self.assertFalse(ScheduleOutcome.objects.exists())
    def test_deadline_internal_notification_is_idempotent(self):
        self.assertEqual(queue_deadlines(self.now),1);self.assertEqual(queue_deadlines(self.now),0)
        row=DeadlineDelivery.objects.get();self.assertEqual(row.kind,'due');self.assertEqual(row.email_state,'off')
        self.assertEqual(WorkNotification.objects.count(),1)
    def test_advance_and_overdue_and_supervisor_escalation(self):
        self.task.due_date=self.day+timedelta(days=3);self.task.save();queue_deadlines(self.now)
        self.assertTrue(DeadlineDelivery.objects.filter(kind='advance').exists())
        self.task.due_date=self.day-timedelta(days=3);self.task.start_date=self.task.due_date;self.task.save();queue_deadlines(self.now)
        self.assertTrue(DeadlineDelivery.objects.filter(kind='overdue',user=self.worker).exists())
        self.assertTrue(DeadlineDelivery.objects.filter(kind='escalation',user=self.lead).exists())
    def test_review_alert_goes_to_current_approver_not_worker(self):
        self.task.status='review';self.task.review_started_at=self.now-timedelta(days=2);self.task.save()
        ApprovalStep.objects.create(task=self.task,approver=self.lead,approver_name='팀장',position=1)
        queue_deadlines(self.now)
        self.assertTrue(DeadlineDelivery.objects.filter(user=self.lead,phase='review').exists())
        self.assertFalse(DeadlineDelivery.objects.filter(user=self.worker).exists())
    def test_inactive_tasks_and_finished_participant_and_disabled_account_excluded(self):
        for status in ['draft','done','cancelled']:
            self.task.status=status;self.task.save();self.assertEqual(queue_deadlines(self.now),0)
        self.task.status='doing';self.task.save();self.worker.is_active=False;self.worker.save()
        self.assertEqual(queue_deadlines(self.now),0)
        self.worker.is_active=True;self.worker.save();self.task.assignments.update(completed_at=self.now)
        self.assertEqual(queue_deadlines(self.now),0)
    def test_weekend_and_send_hour(self):
        self.policy.weekdays_only=True;self.policy.save()
        self.assertEqual(queue_deadlines(self.now.replace(hour=8)),0)
        self.assertEqual(queue_deadlines(datetime(2026,11,14,10,tzinfo=SEOUL)),0)
    def test_pilot_recipient_scope_prevents_other_staff_alerts(self):
        self.policy.recipient_employee=self.foreign;self.policy.save()
        self.assertEqual(queue_deadlines(self.now),0)
        self.policy.recipient_employee=self.emp;self.policy.save()
        self.assertEqual(queue_deadlines(self.now),1)
    def test_date_change_invalidates_pending_mail_and_recalculates(self):
        self.policy.send_email=True;self.policy.save();queue_deadlines(self.now)
        self.task.due_date+=timedelta(days=3);self.task.save()
        with patch('accounts.services.deliver') as send:deliver_deadlines(self.now);send.assert_not_called()
        self.assertEqual(DeadlineDelivery.objects.get().email_state,'skipped')
        queue_deadlines(self.now);self.assertTrue(DeadlineDelivery.objects.filter(kind='advance',email_state='pending').exists())
    def test_daily_mail_summary_once_and_escaped(self):
        self.policy.send_email=True;self.policy.save()
        second=Task.objects.create(title='<script>원문</script>',creator=self.admin,assignee=self.emp,workflow_version=2,start_date=self.day,due_date=self.day)
        TaskParticipation.objects.create(task=second,employee=self.emp)
        queue_deadlines(self.now);deliver_deadlines(self.now);deliver_deadlines(self.now)
        self.assertEqual(len(mail.outbox),1)
        self.assertIn('교육 일정',mail.outbox[0].body)
        self.assertIn('&lt;script&gt;',mail.outbox[0].alternatives[0].content)
        self.assertEqual(DeadlineDelivery.objects.filter(email_state='sent').count(),2)
    def test_ambiguous_mail_does_not_retry(self):
        self.policy.send_email=True;self.policy.save();queue_deadlines(self.now)
        with patch('accounts.services.deliver',side_effect=RuntimeError('provider secret')) as send:
            deliver_deadlines(self.now);deliver_deadlines(self.now);self.assertEqual(send.call_count,1)
        self.assertEqual(DeadlineDelivery.objects.get().email_state,'unknown')
    def test_policy_disable_or_cancel_skips_queued_mail(self):
        self.policy.send_email=True;self.policy.save();queue_deadlines(self.now)
        self.task.status='cancelled';self.task.save()
        with patch('accounts.services.deliver') as send:deliver_deadlines(self.now);send.assert_not_called()
    def test_late_registration_and_schedule_history_preserve_first_plan(self):
        with patch('tasks.schedule_control.timezone.now',return_value=self.now):record(self.task,self.admin,'created','등록')
        registration=ScheduleRegistration.objects.get(task=self.task);self.assertTrue(registration.late)
        initial=registration.initial_plan.copy();before=snapshot(self.task);self.task.start_date=self.day;self.task.save()
        event=record(self.task,self.admin,'changed','교육 일정 조정',{'before':before})
        self.assertTrue(event.snapshot['schedule_change']);registration.refresh_from_db();self.assertEqual(registration.initial_plan,initial)
        self.assertIn('start_date',event.snapshot['schedule_changes'])
    def test_draft_not_flagged_until_published_and_existing_not_backfilled(self):
        self.task.status='draft';self.task.save();record(self.task,self.admin,'created','초안')
        self.assertFalse(ScheduleRegistration.objects.exists())
        before=snapshot(self.task);self.task.status='doing';self.task.save()
        record(self.task,self.admin,'changed','공개',{'before':before});self.assertTrue(ScheduleRegistration.objects.exists())
    def test_schedule_change_reason_required_via_real_edit(self):
        self.client.force_login(self.admin)
        data={'title':self.task.title,'assignee':self.emp.pk,'start_date':self.day,'due_date':self.day,'status':'doing','importance':'normal','priority':3,'management_mode':'event','revision':0,'no_approval':'on'}
        response=self.client.post(f'/tasks/{self.task.pk}/edit/',data);self.assertEqual(response.status_code,200)
        self.assertContains(response,'일정 또는 마감일을 변경한 사유')
        data['change_reason']='고객 요청으로 일정 조정';self.assertEqual(self.client.post(f'/tasks/{self.task.pk}/edit/',data).status_code,302)
    def test_outcome_requires_reason_not_attendance_inference_and_history(self):
        path=f'/work/schedule-control/task/{self.task.pk}/'
        self.assertEqual(self.client.post(path,{'result':'absent','reason':''}).status_code,200)
        self.assertFalse(ScheduleOutcome.objects.exists())
        self.assertEqual(self.client.post(path,{'result':'absent','reason':'고객 긴급 대응'}).status_code,302)
        self.task.refresh_from_db();self.assertEqual(self.task.status,'doing')
        row=ScheduleOutcome.objects.get();self.client.force_login(self.lead)
        self.assertEqual(self.client.post(f'/work/schedule-control/outcomes/{row.pk}/review/',{'note':'사유 확인'}).status_code,302)
        self.client.force_login(self.worker)
        self.client.post(path,{'result':'explanation','reason':'설명 정정'})
        row.refresh_from_db();self.assertIsNone(row.reviewed_at)
        self.assertEqual(self.task.events.filter(snapshot__has_key='schedule_outcome').count(),2)
    def test_foreign_access_and_policy_permissions(self):
        self.assertEqual(self.client.get('/work/schedule-control/policy/').status_code,403)
        self.client.force_login(self.outside)
        self.assertEqual(self.client.get(f'/work/schedule-control/task/{self.task.pk}/').status_code,404)
        self.assertNotContains(self.client.get('/work/schedule-control/'),'교육 일정')
    def test_missing_expectation_and_employee_cannot_waive_it(self):
        item=ScheduleExpectation.objects.create(title='정기점검 등록',employee=self.emp,creator=self.admin,expected_date=self.day,register_by=self.now-timedelta(hours=1))
        with patch('tasks.schedule_control.timezone.now',return_value=self.now):self.assertContains(self.client.get('/work/schedule-control/'),'미등록 · 확인 필요')
        path=f'/work/schedule-control/expectations/{item.pk}/'
        self.assertEqual(self.client.post(path,{'reason':'임의 면제'}).status_code,200)
        self.assertEqual(self.client.post(path,{'task':self.task.pk,'reason':'해당 업무에 등록'}).status_code,302)
        item.refresh_from_db();self.assertEqual(item.task_id,self.task.pk)
        self.client.force_login(self.outside);self.assertEqual(self.client.get(path).status_code,404)
    def test_root_policy_form_limits_and_admin_expectation(self):
        self.client.force_login(self.admin)
        self.assertEqual(self.client.post('/work/schedule-control/policy/',{'advance_days':999,'send_hour':30,'escalation_days':0}).status_code,200)
        self.assertEqual(self.client.post('/work/schedule-control/expectations/new/',{'title':'교육 사전 등록','employee':self.emp.pk,'expected_date':self.day,'register_by':self.now.isoformat()}).status_code,302)
