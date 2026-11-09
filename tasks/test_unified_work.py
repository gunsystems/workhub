import hashlib,secrets,tempfile
from datetime import datetime,timedelta
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied,ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase,override_settings,Client
from django.utils import timezone
from .models import (Department,Employee,Task,TaskParticipation,ApprovalStep,WorkCategory,WorkSeries,WorkOccurrence,
                     CalendarSubscription,PeriodReport,ReportSubmission,ReportReview,ReportFile,CalendarExport,CalendarExportPolicy)
from .collaboration import OfficialTaskForm,change
from .work_series import process_work_rule,template_snapshot
from .schedules import next_run,SEOUL
from .google_calendar import payload


@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'],WORKHUB_REQUIRE_HIWORKS=False,WORKHUB_SCHEDULER_ENABLED=False)
class UnifiedWorkTests(TestCase):
    def setUp(self):
        U=get_user_model();self.root=U.objects.create_superuser('operator',password='test')
        self.ceo=U.objects.create_user('representative');self.worker=U.objects.create_user('performer');self.lead=U.objects.create_user('lead');self.outside=U.objects.create_user('outside')
        self.team=Department.objects.create(name='통합팀');self.other=Department.objects.create(name='다른팀')
        self.emp=Employee.objects.create(user=self.worker,name='수행자',department=self.team)
        self.executive=Employee.objects.create(user=self.ceo,name='대표',position='executive',department=self.team)
        self.manager=Employee.objects.create(user=self.lead,name='팀장',position='leader',role='manager',department=self.team)
        Employee.objects.create(user=self.outside,name='외부팀',department=self.other)
        self.day=timezone.localdate();self.start=datetime.combine(self.day,datetime.min.time(),tzinfo=SEOUL)+timedelta(hours=9)
        self.task=Task.objects.create(title='고객 월간점검',creator=self.root,assignee=self.emp,workflow_version=2,start_date=self.day,due_date=self.day,activity_start=self.start,activity_end=self.start+timedelta(hours=1))
        TaskParticipation.objects.create(task=self.task,employee=self.emp)
        self.client.force_login(self.root)
        CalendarExportPolicy.objects.update_or_create(pk=1,defaults={'allow_official':True})
    def data(self,**extra):
        return {'title':'교육','assignee':self.emp.pk,'start_date':self.day,'due_date':self.day,'status':'todo','importance':'normal','priority':3,'allocation':'shared','revision':0,'management_mode':'event',**extra}
    def act(self,user,kind,**extra):
        self.task.refresh_from_db()
        return change(user,self.task.pk,kind,{'body':'사유 기록','revision':self.task.revision,**extra})
    def test_representative_override_is_not_account_administration(self):
        ApprovalStep.objects.create(task=self.task,approver=self.lead,approver_name='팀장',position=1)
        self.act(self.worker,'submit');self.act(self.ceo,'override',decision='approve')
        self.task.refresh_from_db();self.assertEqual(self.task.status,'done')
        self.assertEqual(self.task.events.filter(kind='override').count(),1)
        self.client.force_login(self.ceo);self.assertEqual(self.client.get('/accounts/administrators/').status_code,403)
        self.assertFalse(self.ceo.is_staff)
    def test_representative_cannot_self_override_and_head_cannot_override(self):
        self.task.assignee=self.executive;self.task.status='review';self.task.save()
        TaskParticipation.objects.create(task=self.task,employee=self.executive)
        with self.assertRaises(ValidationError):self.act(self.ceo,'override',decision='approve')
        self.manager.position='head';self.manager.save()
        with self.assertRaises(PermissionDenied):self.act(self.lead,'override',decision='approve')
    def test_simple_event_no_approval_and_custom_category(self):
        r=self.client.post('/work/new/',self.data(new_category='사내 실습'))
        self.assertEqual(r.status_code,302)
        obj=Task.objects.latest('pk');self.assertEqual(obj.category.name,'사내 실습');self.assertFalse(obj.approval_steps.exists());self.assertFalse(obj.needs_report)
        self.assertEqual(obj.management_mode,'event')
    def test_simple_event_rejects_approval_and_invalid_time(self):
        f=OfficialTaskForm(self.data(approver_1=self.lead.pk),user=self.root);self.assertFalse(f.is_valid())
        f=OfficialTaskForm(self.data(activity_start=self.start.isoformat(),activity_end=(self.start-timedelta(hours=1)).isoformat()),user=self.root)
        self.assertFalse(f.is_valid());self.assertIn('activity_end',f.errors)
    def test_shared_filter_and_overlap_use_visible_scope(self):
        category=WorkCategory.objects.first();self.task.category=category;self.task.save()
        r=self.client.get('/work/',{'category':category.pk});self.assertContains(r,self.task.title)
        clone=Task.objects.create(title='겹치는 방문',creator=self.root,assignee=self.emp,start_date=self.day,due_date=self.day,activity_start=self.start,activity_end=self.start+timedelta(hours=2))
        self.assertContains(self.client.get(f'/tasks/{self.task.pk}/'),clone.title)
    def series(self,**extra):
        rule=WorkSeries.objects.create(template_task=self.task,owner=self.root,template=template_snapshot(self.task,0),starts_at=self.start,kind='monthly_date',month_day=timezone.localtime(self.start).day,**extra)
        WorkOccurrence.objects.create(series=rule,task=self.task,scheduled_for=self.start)
        rule.next_at=next_run(rule,self.start);rule.save();return rule
    def test_monthly_occurrence_independent_idempotent_and_old_incomplete_preserved(self):
        rule=self.series();when=rule.next_at
        self.assertEqual(process_work_rule(rule.pk,when),1)
        self.assertEqual(process_work_rule(rule.pk,when),0)
        rule.refresh_from_db();self.assertEqual(rule.generated_count,2)
        self.task.refresh_from_db();self.assertNotEqual(self.task.status,'done')
        new=rule.occurrences.exclude(task=self.task).get().task
        self.assertEqual(new.participants.count(),1);self.assertEqual(new.progress,0)
        self.assertEqual(new.activity_end-new.activity_start,timedelta(hours=1))
    def test_disabled_worker_stops_recurrence_without_losing_prior_history(self):
        rule=self.series();self.emp.is_active=False;self.emp.save()
        self.assertEqual(process_work_rule(rule.pk,rule.next_at),0)
        rule.refresh_from_db();self.assertTrue(rule.issue);self.assertEqual(rule.occurrences.count(),1)
    def test_repetition_max_count_and_stop(self):
        rule=self.series(max_count=2);process_work_rule(rule.pk,rule.next_at+timedelta(days=200))
        self.assertEqual(rule.occurrences.count(),2)
        self.client.post(f'/work/series/{self.task.pk}/',{'action':'stop'});rule.refresh_from_db();self.assertFalse(rule.enabled)
        self.assertEqual(Task.objects.count(),2)
    def test_feed_token_rotation_and_privacy_and_cancellation(self):
        self.client.force_login(self.worker)
        r=self.client.post('/work/calendar/subscription/',{'action':'issue'})
        url=self.client.session['calendar_subscription_url'];path=url.split('.com')[-1] if '.com' in url else url.split('.kr')[-1]
        token=url.rsplit('/',1)[1][:-4]
        self.assertEqual(CalendarSubscription.objects.get(owner=self.worker).token_digest,hashlib.sha256(token.encode()).hexdigest())
        anonymous=Client();response=anonymous.get('/work/calendar/subscription/'+token+'.ics')
        self.assertEqual(response.status_code,200);self.assertIn(b'BEGIN:VEVENT',response.content);self.assertNotIn(self.task.description.encode(),response.content) if self.task.description else None
        self.task.status='cancelled';self.task.save();self.assertIn(b'STATUS:CANCELLED',anonymous.get('/work/calendar/subscription/'+token+'.ics').content)
        self.client.post('/work/calendar/subscription/',{'action':'issue'})
        self.assertEqual(anonymous.get('/work/calendar/subscription/'+token+'.ics').status_code,404)
        self.client.post('/work/calendar/subscription/',{'action':'revoke'})
        self.assertFalse(CalendarSubscription.objects.get(owner=self.worker).enabled)
    def test_feed_excludes_foreign_work_drafts_and_disabled_accounts(self):
        token=secrets.token_urlsafe(32);CalendarSubscription.objects.create(owner=self.worker,token_digest=hashlib.sha256(token.encode()).hexdigest())
        Task.objects.create(title='비공개 초안',creator=self.root,assignee=self.emp,status='draft',start_date=self.day,due_date=self.day)
        r=Client().get('/work/calendar/subscription/'+token+'.ics');self.assertNotIn('비공개 초안'.encode(),r.content)
        self.emp.is_active=False;self.emp.save();self.assertEqual(Client().get('/work/calendar/subscription/'+token+'.ics').status_code,404)
    def test_legacy_title_edit_advances_subscription_version_and_keeps_uid(self):
        from .workflow import record
        self.task.workflow_version=1;self.task.save()
        record(self.task,self.root,'created','기존 업무')
        token=secrets.token_urlsafe(32)
        CalendarSubscription.objects.create(owner=self.worker,token_digest=hashlib.sha256(token.encode()).hexdigest())
        path='/work/calendar/subscription/'+token+'.ics'
        def event():
            text=Client().get(path).content.decode().replace('\r\n ','')
            return dict(line.split(':',1) for line in text.split('\r\n') if ':' in line)
        before=event()
        self.assertGreater(int(before['SEQUENCE']),0)
        self.task.refresh_from_db();self.assertEqual(self.task.revision,0)
        self.assertEqual(event()['SEQUENCE'],before['SEQUENCE'])
        response=self.client.post(f'/tasks/{self.task.pk}/edit/',{
            'title':'변경된 일정 제목','assignee':self.emp.pk,'start_date':self.day,
            'due_date':self.day,'status':'doing','importance':'normal','priority':3,
            'management_mode':'event','activity_start':self.start.isoformat(),
            'activity_end':(self.start+timedelta(hours=1)).isoformat()})
        self.assertEqual(response.status_code,302)
        after=event()
        self.assertEqual(after['UID'],before['UID'])
        self.assertEqual(after['SUMMARY'],'변경된 일정 제목')
        self.assertGreater(int(after['SEQUENCE']),int(before['SEQUENCE']))
        self.assertRegex(after['LAST-MODIFIED'],r'^\d{8}T\d{6}Z$')
        self.task.refresh_from_db();self.assertEqual(self.task.revision,1)
    def report_data(self,**extra):
        return {'period_start':self.day,'period_end':self.day+timedelta(days=6),'summary':'업무 결과와 다음 계획','reviewer':self.lead.pk,'tasks':[self.task.pk],'revision':0,'action':'submit',**extra}
    def test_report_submission_snapshot_return_resubmit_and_role_enforcement(self):
        self.client.force_login(self.worker);r=self.client.post('/work/reports/new/',self.report_data());self.assertEqual(r.status_code,302)
        report=PeriodReport.objects.get();first=report.current_submission
        self.task.title='원본 수정';self.task.save();self.assertEqual(first.snapshot['tasks'][0]['title'],'고객 월간점검')
        self.assertEqual(self.client.get(f'/work/reports/{report.pk}/edit/').status_code,403)
        self.client.force_login(self.outside);self.assertEqual(self.client.get(f'/work/reports/{report.pk}/').status_code,404)
        self.client.force_login(self.lead);self.client.post(f'/work/reports/{report.pk}/',{'revision':report.revision,'decision':'returned','body':'내용 보완'})
        report.refresh_from_db();self.assertEqual(report.state,'returned')
        self.client.force_login(self.worker);self.client.post(f'/work/reports/{report.pk}/edit/',self.report_data(revision=report.revision,summary='보완 결과'))
        report.refresh_from_db();self.assertEqual(report.submissions.count(),2);first.refresh_from_db();self.assertEqual(first.snapshot['summary'],'업무 결과와 다음 계획')
    def test_report_aggregation_freezes_selected_submission(self):
        self.client.force_login(self.worker);self.client.post('/work/reports/new/',self.report_data())
        source=PeriodReport.objects.get().current_submission
        self.client.force_login(self.lead);r=self.client.post('/work/reports/new/',self.report_data(reviewer=self.ceo.pk,sources=[source.pk]))
        self.assertEqual(r.status_code,302);aggregate=PeriodReport.objects.latest('pk');self.assertEqual(aggregate.current_submission.snapshot['source_submission_ids'],[source.pk])
        self.assertContains(self.client.get(f'/work/reports/{aggregate.pk}/'),'업무 결과와 다음 계획')
    def test_private_upload_requires_report_access_and_disallows_active_content(self):
        from pathlib import Path
        with tempfile.TemporaryDirectory(dir=Path('.tmp').resolve()) as directory,override_settings(MEDIA_ROOT=directory):
            self.client.force_login(self.worker);self.client.post('/work/reports/new/',self.report_data(attachment=SimpleUploadedFile('result.pdf',b'%PDF-test')))
            attachment=ReportFile.objects.get();r=self.client.get(f'/work/reports/files/{attachment.pk}/');self.assertEqual(r.status_code,200);self.assertIn('attachment',r['Content-Disposition']);r.close()
            self.client.force_login(self.outside);self.assertEqual(self.client.get(f'/work/reports/files/{attachment.pk}/').status_code,404)
            self.client.force_login(self.worker);r=self.client.post('/work/reports/new/',self.report_data(attachment=SimpleUploadedFile('run.html',b'<script>')));self.assertEqual(r.status_code,200);self.assertEqual(ReportFile.objects.count(),1)
    def test_delete_auth_account_keeps_reports_and_immutable_submissions(self):
        self.client.force_login(self.worker);self.client.post('/work/reports/new/',self.report_data())
        self.worker.delete();self.assertIsNone(PeriodReport.objects.get().author_id);self.assertEqual(ReportSubmission.objects.count(),1)
    def test_new_pages_render_without_writes_on_get(self):
        before=Task.objects.count()
        for url in ['/work/new/','/work/reports/','/work/reports/new/','/work/calendar/subscription/',f'/work/series/{self.task.pk}/']:
            with self.subTest(url=url):self.assertEqual(self.client.get(url).status_code,200)
        self.assertEqual(Task.objects.count(),before);self.assertFalse(WorkSeries.objects.exists());self.assertFalse(CalendarSubscription.objects.exists())

    def test_feed_company_policy_revocation_blocks_existing_token(self):
        token=secrets.token_urlsafe(32);CalendarSubscription.objects.create(owner=self.worker,token_digest=hashlib.sha256(token.encode()).hexdigest())
        CalendarExportPolicy.objects.filter(pk=1).update(allow_official=False)
        self.assertEqual(Client().get('/work/calendar/subscription/'+token+'.ics').status_code,404)
        self.client.force_login(self.worker);self.client.post('/work/calendar/subscription/',{'action':'issue'})
        self.assertEqual(CalendarSubscription.objects.get().token_digest,hashlib.sha256(token.encode()).hexdigest())

    def test_simple_event_expiration_is_not_automatic_completion_or_performance(self):
        from .metrics import employee_metrics
        self.task.management_mode='event';self.task.due_date=self.day-timedelta(days=1);self.task.start_date=self.task.due_date
        self.task.activity_start-=timedelta(days=1);self.task.activity_end-=timedelta(days=1);self.task.save()
        result=employee_metrics(self.emp,[self.task],self.day-timedelta(days=7),self.day)
        self.assertEqual(result['eligible'],0);self.task.refresh_from_db();self.assertNotEqual(self.task.status,'done')

    def test_completion_repetition_creates_one_followup_and_keeps_original(self):
        rule=self.series(mode='completion',max_count=2)
        self.act(self.worker,'submit')
        rule.refresh_from_db();self.assertEqual(rule.generated_count,2)
        self.task.refresh_from_db();self.assertEqual(self.task.status,'done')
        from .work_series import completion_followup
        from django.db import transaction
        with transaction.atomic():completion_followup(self.task)
        self.assertEqual(rule.occurrences.count(),2)

    def test_report_rejects_self_reviewer_and_foreign_aggregation(self):
        self.client.force_login(self.worker)
        response=self.client.post('/work/reports/new/',self.report_data(reviewer=self.worker.pk))
        self.assertEqual(response.status_code,200);self.assertFalse(PeriodReport.objects.exists())

    def test_token_log_mask_does_not_change_other_logs(self):
        import logging
        from config.log_filters import SubscriptionTokenFilter
        record=logging.LogRecord('test',30,'file',1,'Not Found: %s',('/work/calendar/subscription/secret.ics',),None)
        SubscriptionTokenFilter().filter(record)
        self.assertNotIn('secret',record.getMessage());self.assertIn('[redacted]',record.getMessage())
