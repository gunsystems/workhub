from django.test import TestCase, override_settings
from . import test_report_grouping as grouping_fixtures
from .models import PeriodReport, ReportSubmission, ReportReview


@override_settings(WORKHUB_TEAM_REPORT_AGGREGATION=False, WORKHUB_REQUIRE_HIWORKS=False,
                   WORKHUB_SCHEDULER_ENABLED=False,
                   PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class EmployeeReportReviewTests(TestCase):
    setUp=grouping_fixtures.ReportGroupingTests.setUp
    source=grouping_fixtures.ReportGroupingTests.source
    payload=grouping_fixtures.ReportGroupingTests.payload

    def test_list_shows_frozen_employee_content_and_feedback_without_aggregation(self):
        sub=self.source(summary='提出原文 <script>alert(1)</script>',plan='다음 주 지원',issues='장애 지원 필요')
        sub.report.summary='제출 뒤 수정 중인 내용';sub.report.save()
        ReportReview.objects.create(submission=sub,author=self.leader,decision='returned',body='교육 시간을 보완해 주세요')
        response=self.client.get('/work/reports/',{'week':str(self.start)})
        self.assertContains(response,'직원별 주간보고');self.assertContains(response,'다음 주 지원')
        self.assertContains(response,'장애 지원 필요');self.assertContains(response,'교육 시간을 보완해 주세요')
        self.assertContains(response,'&lt;script&gt;');self.assertNotContains(response,'<script>alert(1)</script>')
        self.assertNotContains(response,'제출 뒤 수정 중인 내용');self.assertNotContains(response,'팀 보고 취합</a>')

    def test_deferred_entry_redirects_and_old_group_post_cannot_write(self):
        sub=self.source();before=(PeriodReport.objects.count(),ReportSubmission.objects.count())
        response=self.client.get('/work/reports/team/new/')
        self.assertRedirects(response,'/work/reports/',fetch_redirect_response=False)
        self.assertEqual(self.client.post('/work/reports/new/',self.payload([sub])).status_code,403)
        self.assertEqual(before,(PeriodReport.objects.count(),ReportSubmission.objects.count()))

    def test_old_form_cannot_attach_sources_but_existing_sources_survive_edit(self):
        sub=self.source()
        data=self.payload([sub],action='draft')
        response=self.client.post('/work/reports/new/',data);self.assertEqual(response.status_code,302)
        report=PeriodReport.objects.get(author=self.leader);self.assertFalse(report.source_submissions.exists())
        report.source_submissions.add(sub)
        data['sources']=[];data['revision']=report.revision
        response=self.client.post(f'/work/reports/{report.pk}/edit/',data)
        self.assertEqual(response.status_code,302);self.assertEqual(list(report.source_submissions.all()),[sub])

    def test_worker_and_other_team_cannot_read_employee_reports(self):
        self.source(summary='비공개 팀 제출 내용')
        self.client.force_login(self.outside)
        response=self.client.get('/work/reports/',{'week':str(self.start)})
        self.assertNotContains(response,'비공개 팀 제출 내용');self.assertNotContains(response,'id="employee-reports-title"')
        self.client.force_login(self.worker)
        response=self.client.get('/work/reports/',{'week':str(self.start)})
        self.assertNotContains(response,'id="employee-reports-title"')

    def test_report_form_does_not_offer_grouping(self):
        response=self.client.get('/work/reports/weekly/new/')
        self.assertNotContains(response,'data-report-grouping')
        self.assertContains(response,'주간업무보고 작성')

    def test_report_choices_and_legacy_history_use_current_labels_without_rewriting(self):
        response=self.client.get('/work/reports/new/')
        choices=dict(response.context['form'].fields['report_type'].choices)
        self.assertEqual(choices['individual'],'건별 보고')
        self.assertEqual(choices['period'],'기간 지정 보고')
        self.assertContains(response,'보고 기간은 직접 변경할 수 있습니다.')
        sub=self.source();sub.report.report_type='individual';sub.report.save()
        sub.snapshot.update(report_type='individual',report_type_label='개별 보고');sub.save()
        response=self.client.get(f'/work/reports/{sub.report_id}/')
        self.assertContains(response,'건별 보고');self.assertNotContains(response,'개별 보고')
        sub.refresh_from_db();self.assertEqual(sub.snapshot['report_type_label'],'개별 보고')
