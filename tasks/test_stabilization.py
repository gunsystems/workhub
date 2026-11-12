"""Boundary cases found during the existing-feature stabilization review."""
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.core import mail
from django.test import TestCase, override_settings
from django.utils import timezone
from accounts.models import AccessProfile
from . import test_assignment_ack as ack_fixtures
from .models import Employee, TaskParticipation, WorkSeries
from .collaboration import change


@override_settings(WORKHUB_REQUIRE_HIWORKS=False, WORKHUB_SCHEDULER_ENABLED=False,
                   EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class WorkflowBoundaryTests(TestCase):
    setUp = ack_fixtures.AssignmentAckTests.setUp

    def helper(self):
        user = get_user_model().objects.create_user('replacement', email='replacement@example.test')
        return Employee.objects.create(user=user, name='Replacement', department=self.emp.department)

    def test_handover_recalculates_progress_and_requires_fresh_ack(self):
        now = timezone.now()
        self.part.accepted_at = now
        self.part.progress = 80
        self.part.save()
        self.task.progress = 80
        self.task.status = 'doing'
        self.task.last_report_at = now
        self.task.save()
        target = self.helper()
        change(self.owner, self.task.pk, 'handover', {
            'revision': self.task.revision, 'body': 'Replacement required', 'replacement': target.user})
        self.task.refresh_from_db()
        self.assertEqual(self.task.progress, 0)
        self.assertIsNone(self.task.last_report_at)
        self.assertTrue(self.task.awaiting_acceptance)
        self.assertEqual(self.task.assignments.get().employee, target)
        self.assertEqual(self.task.events.get().snapshot['before']['progress'], '80')

    def test_shared_handover_retains_other_workers_progress(self):
        helper = self.helper()
        TaskParticipation.objects.create(task=self.task, employee=helper,
                                         accepted_at=timezone.now(), progress=60)
        self.part.progress = 80
        self.part.accepted_at = timezone.now()
        self.part.save()
        self.task.progress = 70
        self.task.status = 'doing'
        self.task.save()
        replacement_user = get_user_model().objects.create_user('new-worker')
        replacement = Employee.objects.create(user=replacement_user, name='New', department=self.emp.department)
        change(self.owner, self.task.pk, 'handover', {
            'revision': 0, 'body': 'Keep shared work', 'replacement': replacement_user})
        self.task.refresh_from_db()
        self.assertEqual(self.task.progress, 30)
        self.assertEqual(self.task.assignments.get(employee=helper).progress, 60)
        self.assertIsNone(self.task.assignments.get(employee=replacement).accepted_at)

    def test_editing_performers_recalculates_visible_progress(self):
        self.part.progress=80
        self.part.accepted_at=timezone.now()
        self.part.save()
        self.task.progress=80
        self.task.save()
        target=self.helper()
        self.client.force_login(self.owner)
        response=self.client.post(f'/tasks/{self.task.pk}/edit/',{
            'title':self.task.title,'assignee':self.emp.pk,'start_date':self.task.start_date,
            'due_date':self.task.due_date,'status':'doing','importance':'normal','priority':3,
            'allocation':'shared','revision':0,'management_mode':'report','no_approval':'on',
            'collaborators':[target.pk],'change_reason':'Add a collaborator'})
        self.assertEqual(response.status_code,302)
        self.task.refresh_from_db()
        self.assertEqual(self.task.progress,40)
        self.assertEqual(self.task.assignments.get(employee=self.emp).progress,80)

    def test_pending_assignment_email_obeys_latest_channel_setting(self):
        from .assignment_mail import enqueue, deliver_pending
        from .models import AssignmentMail
        self.worker.email = 'worker@example.test'
        self.worker.save()
        AccessProfile.objects.create(user=self.worker, email=self.worker.email,
                                     verified_at=timezone.now(), require_mfa=False)
        enqueue(self.task, self.owner)
        self.task.assignment_email = False
        self.task.save()
        deliver_pending()
        self.assertEqual(len(mail.outbox), 0)
        self.assertEqual(AssignmentMail.objects.get().state, 'skipped')

    def test_calendar_extreme_dates_do_not_overflow_previous_next_navigation(self):
        self.client.force_login(self.owner)
        for day in ['0001-01-01','9999-12-31']:
            for view in ['month','week','day']:
                with self.subTest(day=day,view=view):
                    response=self.client.get('/',{'date':day,'view':view})
                    self.assertEqual(response.status_code,200)
                    self.assertEqual(response.context['selected'],timezone.localdate())

    def test_one_failed_schedule_does_not_block_remaining_work_and_notifications(self):
        from .scheduler import tick
        from .work_series import template_snapshot
        rules = [WorkSeries.objects.create(template_task=self.task, owner=self.owner,
                 template=template_snapshot(self.task, 0), starts_at=timezone.now(),
                 kind='daily', next_at=timezone.now()) for _ in range(2)]
        with patch('tasks.work_series.process_work_rule', side_effect=[ValueError('private-failure'), 1]) as work, \
             patch('accounts.hiworks_access.refresh_directory'), \
             patch('tasks.assignment_mail.deliver_pending') as assignment, \
             patch('tasks.quick.deliver_pending') as quick, \
             self.assertLogs('tasks.scheduler', level='WARNING') as logs:
            result = tick(sync_google=False)
        self.assertEqual(work.call_count, 2)
        self.assertEqual(result['created'], 1)
        assignment.assert_called_once()
        quick.assert_called_once()
        self.assertNotIn('private-failure', ' '.join(logs.output))

    def test_delivery_stage_failure_does_not_starve_quick_or_offboarding(self):
        from .scheduler import tick
        with patch('accounts.hiworks_access.refresh_directory'), \
             patch('tasks.notices.deliver_pending', side_effect=ValueError('private-provider-response')), \
             patch('tasks.quick.deliver_pending') as quick, \
             patch('tasks.offboarding.deliver_notices') as offboarding, \
             self.assertLogs('tasks.scheduler', level='WARNING') as logs:
            result=tick(sync_google=False)
        self.assertIn('notice_delivery',result['failed_stages'])
        quick.assert_called_once()
        offboarding.assert_called_once()
        self.assertNotIn('private-provider-response',' '.join(logs.output))


@override_settings(WORKHUB_REQUIRE_HIWORKS=False, WORKHUB_SCHEDULER_ENABLED=False)
class WeeklyDeadlineTests(TestCase):
    def setUp(self):
        from . import test_weekly_reporting as fixtures
        fixtures.WeeklyReportTests.setUp(self)

    def test_missing_is_pending_before_deadline_and_overdue_after(self):
        from datetime import timedelta
        from .weekly_reporting import submission_board,submission_deadline
        deadline=submission_deadline(self.week)
        self.assertEqual(deadline.weekday(),4)
        self.assertEqual(deadline.hour,17)
        before=next(r for r in submission_board(self.leader,self.week,deadline-timedelta(seconds=1)) if r['employee']==self.emp)
        after=next(r for r in submission_board(self.leader,self.week,deadline+timedelta(seconds=1)) if r['employee']==self.emp)
        self.assertFalse(before['overdue'])
        self.assertTrue(after['overdue'])
        self.assertEqual(after['timing_label'],'미제출 · 기한 경과')

    def test_resubmission_does_not_reclassify_an_on_time_first_submission(self):
        from datetime import timedelta
        from .models import PeriodReport,ReportSubmission
        from .weekly_reporting import submission_board,submission_deadline
        deadline=submission_deadline(self.week)
        report=PeriodReport.objects.create(author=self.worker,reviewer=self.leader,report_type='weekly',
            period_start=self.week,period_end=self.week+timedelta(days=6),summary='Summary',
            state='submitted',submitted_at=deadline+timedelta(days=1))
        original=ReportSubmission.objects.create(report=report,version=1,snapshot={},created_at=deadline-timedelta(minutes=5))
        ReportSubmission.objects.create(report=report,version=3,snapshot={},created_at=deadline+timedelta(days=1))
        row=next(r for r in submission_board(self.leader,self.week) if r['employee']==self.emp)
        self.assertFalse(row['late'])
        self.assertEqual(row['first_submitted_at'],original.created_at)
        report.submissions.filter(pk=original.pk).update(created_at=deadline+timedelta(minutes=1))
        row=next(r for r in submission_board(self.leader,self.week) if r['employee']==self.emp)
        self.assertTrue(row['late'])
