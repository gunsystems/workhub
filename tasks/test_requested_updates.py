from datetime import timedelta
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.test import TestCase,TransactionTestCase,override_settings
from django.utils import timezone
from accounts.models import AccessProfile
from .models import Department,Employee,Task,PeriodReport,AssignmentMail,WorkNotification
from .reports import ReportForm


@override_settings(WORKHUB_REQUIRE_HIWORKS=False,WORKHUB_SCHEDULER_ENABLED=False,
                   PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class RequestedUpdatesTests(TestCase):
    def setUp(self):
        U=get_user_model();self.root=U.objects.create_superuser('update-operator')
        self.worker=U.objects.create_user('update-worker',email='update@gunsystems.co.kr')
        self.lead_user=U.objects.create_user('update-lead')
        self.team=Department.objects.create(name='수정팀');self.other=Department.objects.create(name='다른수정팀')
        Employee.objects.create(name='검토 직원',user=self.lead_user,department=self.other)
        self.member=Employee.objects.create(name='수정팀원',user=self.worker,department=self.team)
        AccessProfile.objects.create(user=self.worker,email=self.worker.email,verified_at=timezone.now(),require_mfa=False)
        self.client.force_login(self.root);self.day=timezone.localdate()

    def work_data(self,**kwargs):
        return {'title':'알림 채널 검증','assignee':self.member.pk,'start_date':self.day,'due_date':self.day,
                'management_mode':'event','status':'todo','priority':3,'importance':'normal',
                'allocation':'shared','revision':0,**kwargs}

    def report_data(self,**kwargs):
        return {'report_type':'daily','period_start':self.day,'period_end':self.day,'summary':'업무 결과',
                'reviewer':self.lead_user.pk,'revision':0,'action':'submit',**kwargs}

    def test_assignment_can_select_both_email_and_messenger(self):
        with patch('tasks.hiworks.enqueue') as enqueue:
            response=self.client.post('/work/new/',self.work_data(assignment_email='on',assignment_messenger='on'))
        self.assertEqual(response.status_code,302);self.assertEqual(AssignmentMail.objects.count(),1)
        self.assertTrue(enqueue.called)

    def test_assignment_email_only(self):
        with patch('tasks.hiworks.enqueue') as enqueue:
            response=self.client.post('/work/new/',self.work_data(assignment_email='on',assignment_messenger='false'))
        self.assertEqual(response.status_code,302);self.assertEqual(AssignmentMail.objects.count(),1)
        enqueue.assert_not_called()

    def test_assignment_messenger_only(self):
        with patch('tasks.hiworks.enqueue') as enqueue:
            response=self.client.post('/work/new/',self.work_data(assignment_email='false',assignment_messenger='on'))
        self.assertEqual(response.status_code,302);self.assertFalse(AssignmentMail.objects.exists());self.assertTrue(enqueue.called)

    def test_assignment_neither_channel_keeps_in_app_notification(self):
        with patch('tasks.hiworks.enqueue') as enqueue:
            response=self.client.post('/work/new/',self.work_data(assignment_email='false',assignment_messenger='false'))
        self.assertEqual(response.status_code,302);self.assertFalse(AssignmentMail.objects.exists())
        enqueue.assert_not_called();self.assertTrue(WorkNotification.objects.filter(user=self.worker).exists())
        task=Task.objects.get();self.assertFalse(task.assignment_email);self.assertFalse(task.assignment_messenger)

    def test_assignment_old_open_form_retains_channel_defaults(self):
        self.client.post('/work/new/',self.work_data())
        task=Task.objects.get();self.assertTrue(task.assignment_email);self.assertTrue(task.assignment_messenger)

    def test_calendar_entry_posts_the_modern_workflow_and_unchecked_channels(self):
        with patch('tasks.hiworks.enqueue') as enqueue:
            response=self.client.post('/tasks/new/',self.work_data(assignment_channels_present='1'))
        self.assertEqual(response.status_code,302)
        task=Task.objects.get();self.assertEqual(task.workflow_version,2)
        self.assertFalse(task.assignment_email);self.assertFalse(task.assignment_messenger)
        enqueue.assert_not_called()

    def test_invalid_legacy_post_renders_marker_for_next_submission(self):
        response=self.client.post('/work/new/',self.work_data(title=''))
        self.assertContains(response,'name="assignment_channels_present" value="1"')

    def test_legacy_assignment_route_also_obeys_messenger_channel(self):
        data=self.work_data(assignment_email='false',assignment_messenger='on')
        data.pop('allocation')
        with patch('tasks.hiworks.enqueue') as enqueue:
            response=self.client.post('/tasks/new/',data)
        self.assertEqual(response.status_code,302)
        self.assertEqual(Task.objects.get().workflow_version,1)
        self.assertFalse(AssignmentMail.objects.exists());self.assertTrue(enqueue.called)

    def test_report_type_field_and_weekly_default_on_new_form(self):
        self.client.force_login(self.worker)
        response=self.client.get('/work/reports/new/')
        self.assertEqual(response.status_code,200);self.assertContains(response,'보고 구분')
        self.assertEqual(response.context['form']['report_type'].value(),'weekly')
        for label in ['일일 보고','주간 보고','월간 보고','개별 보고','기간 보고']:self.assertContains(response,label)

    def test_report_kind_is_frozen_in_submission_snapshot(self):
        self.client.force_login(self.worker)
        response=self.client.post('/work/reports/new/',self.report_data())
        self.assertEqual(response.status_code,302)
        report=PeriodReport.objects.get();snapshot=report.current_submission.snapshot
        self.assertEqual(snapshot['report_type'],'daily');self.assertEqual(snapshot['report_type_label'],'일일 보고')
        report.report_type='individual';report.save()
        self.assertEqual(report.current_submission.snapshot['report_type'],'daily')

    def test_daily_requires_single_day_and_explicit_blank_type_rejected(self):
        self.assertFalse(ReportForm(self.report_data(period_end=self.day+timedelta(days=1)),user=self.worker).is_valid())
        form=ReportForm(self.report_data(report_type=''),user=self.worker)
        self.assertFalse(form.is_valid());self.assertIn('report_type',form.errors)

    def test_report_legacy_payload_and_existing_report_keep_period_category(self):
        data=self.report_data();data.pop('report_type')
        self.client.force_login(self.worker);response=self.client.post('/work/reports/new/',data)
        self.assertEqual(response.status_code,302);self.assertEqual(PeriodReport.objects.get().report_type,'period')

    def test_report_edit_preserves_previous_dates_and_reviewer(self):
        first=self.day-timedelta(days=30);last=first+timedelta(days=2)
        report=PeriodReport.objects.create(author=self.worker,reviewer=self.lead_user,report_type='individual',
            period_start=first,period_end=last,summary='작성 중')
        self.client.force_login(self.worker)
        form=self.client.get(f'/work/reports/{report.pk}/edit/').context['form']
        self.assertEqual(form.initial['period_start'],first);self.assertEqual(form.initial['period_end'],last)
        self.assertEqual(form.initial['report_type'],'individual');self.assertEqual(form.initial['reviewer'],self.lead_user.pk)
        legacy=self.report_data();legacy.pop('report_type')
        existing=ReportForm(legacy,user=self.worker,instance=report)
        self.assertTrue(existing.is_valid(),existing.errors)
        self.assertEqual(existing.cleaned_data['report_type'],'individual')

    def test_report_list_filters_type_without_revealing_other_private_drafts(self):
        self.client.force_login(self.worker)
        self.client.post('/work/reports/new/',self.report_data())
        self.client.post('/work/reports/new/',self.report_data(report_type='individual'))
        response=self.client.get('/work/reports/',{'week':str(self.day),'report_type':'daily'})
        self.assertEqual(response.status_code,200);self.assertEqual(len(response.context['reports']),1)
        self.assertEqual(response.context['reports'][0].report_type,'daily')

    def test_later_team_leader_automatically_becomes_supervisor(self):
        self.assertIsNone(self.member.supervisor_id)
        leader=Employee.objects.create(name='팀장',department=self.team,position='leader')
        self.member.refresh_from_db();self.assertEqual(self.member.supervisor_id,leader.pk);self.assertTrue(self.member.supervisor_auto)

    def test_head_fallback_then_later_team_leader_is_preferred(self):
        head=Employee.objects.create(name='본부장',department=self.team,position='head')
        self.member.refresh_from_db();self.assertEqual(self.member.supervisor_id,head.pk)
        leader=Employee.objects.create(name='팀장',department=self.team,position='leader')
        self.member.refresh_from_db();leader.refresh_from_db()
        self.assertEqual(self.member.supervisor_id,leader.pk);self.assertEqual(leader.supervisor_id,head.pk)

    def test_manually_selected_supervisor_is_not_overwritten(self):
        manual=Employee.objects.create(name='수동 상위자',department=self.other,position='head')
        self.member.supervisor=manual;self.member.save()
        Employee.objects.create(name='새 팀장',department=self.team,position='leader')
        self.member.refresh_from_db();self.assertEqual(self.member.supervisor_id,manual.pk);self.assertFalse(self.member.supervisor_auto)

    def test_multiple_team_leaders_do_not_pick_arbitrarily(self):
        Employee.objects.create(name='팀장1',department=self.team,position='leader')
        Employee.objects.create(name='팀장2',department=self.team,position='leader')
        self.member.refresh_from_db();self.assertIsNone(self.member.supervisor_id)

    def test_departed_or_moved_leader_reconciles_both_departments(self):
        head=Employee.objects.create(name='본부장',department=self.team,position='head')
        leader=Employee.objects.create(name='팀장',department=self.team,position='leader')
        leader.department=self.other;leader.save()
        self.member.refresh_from_db();self.assertEqual(self.member.supervisor_id,head.pk)
        self.assertIsNone(leader.supervisor_id)

    def test_automatic_supervisor_never_creates_cycle_or_changes_permissions(self):
        leader=Employee.objects.create(name='팀장',department=self.team,position='leader')
        leader.supervisor=self.member;leader.save()
        self.member.refresh_from_db();self.assertIsNone(self.member.supervisor_id)
        self.assertFalse(self.worker.is_staff);self.assertEqual(self.member.role,'employee')

    def test_disabled_manager_falls_back_then_reconnects_after_reactivation(self):
        head=Employee.objects.create(name='본부장',department=self.team,position='head')
        manager_user=get_user_model().objects.create_user('active-manager')
        leader=Employee.objects.create(name='팀장',department=self.team,position='leader',user=manager_user)
        manager_user.is_active=False;manager_user.save()
        self.member.refresh_from_db();self.assertEqual(self.member.supervisor_id,head.pk)
        manager_user.is_active=True;manager_user.save()
        self.member.refresh_from_db();self.assertEqual(self.member.supervisor_id,leader.pk)


class RequestedUpdatesMigrationTests(TransactionTestCase):
    def test_existing_manual_relationship_and_report_snapshot_survive(self):
        from django.db import connection
        from django.db.migrations.executor import MigrationExecutor
        executor=MigrationExecutor(connection)
        previous=[('tasks','0024_company_notices')]
        executor.migrate(previous)
        old=executor.loader.project_state(previous).apps
        try:
            department=old.get_model('tasks','Department').objects.create(name='이전 버전 보존팀')
            leader=old.get_model('tasks','Employee').objects.create(name='기존 팀장',department=department,position='leader')
            member=old.get_model('tasks','Employee').objects.create(name='기존 팀원',department=department,supervisor=leader)
            report=old.get_model('tasks','PeriodReport').objects.create(period_start=timezone.localdate(),period_end=timezone.localdate(),summary='기존 보고')
            submission=old.get_model('tasks','ReportSubmission').objects.create(report=report,version=1,snapshot={'summary':'고정된 원본'})
            task=old.get_model('tasks','Task').objects.create(title='기존 업무',assignee=member,start_date=timezone.localdate(),due_date=timezone.localdate())
        finally:
            current=MigrationExecutor(connection);current.migrate(current.loader.graph.leaf_nodes())
        from .models import ReportSubmission
        employee=Employee.objects.get(pk=member.pk)
        self.assertEqual(employee.supervisor_id,leader.pk);self.assertFalse(employee.supervisor_auto)
        self.assertEqual(PeriodReport.objects.get(pk=report.pk).report_type,'period')
        self.assertEqual(ReportSubmission.objects.get(pk=submission.pk).snapshot,{'summary':'고정된 원본'})
        migrated=Task.objects.get(pk=task.pk)
        self.assertTrue(migrated.assignment_email);self.assertTrue(migrated.assignment_messenger)
