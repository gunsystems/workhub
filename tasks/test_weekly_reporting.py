from datetime import date,datetime,timedelta
from zoneinfo import ZoneInfo
from django.contrib.auth import get_user_model
from django.test import TestCase,override_settings
from .models import Department,Employee,Task,TaskParticipation,PeriodReport,ReportSubmission
from .weekly_reporting import calendar_draft,week_start,submission_board
from .reports import visible_reports

@override_settings(WORKHUB_REQUIRE_HIWORKS=False,WORKHUB_SCHEDULER_ENABLED=False,PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class WeeklyReportTests(TestCase):
    def setUp(self):
        U=get_user_model();self.team=Department.objects.create(name='주간보고팀');self.otherteam=Department.objects.create(name='다른부서')
        self.worker=U.objects.create_user('weekly_worker');self.leader=U.objects.create_user('weekly_leader');self.peer=U.objects.create_user('weekly_peer');self.outside=U.objects.create_user('weekly_outside')
        self.emp=Employee.objects.create(name='보고직원',user=self.worker,department=self.team)
        self.lead=Employee.objects.create(name='팀장',user=self.leader,department=self.team,position='leader',role='manager')
        self.peeremp=Employee.objects.create(name='동료',user=self.peer,department=self.team)
        self.other=Employee.objects.create(name='다른직원',user=self.outside,department=self.otherteam)
        self.week=date(2026,11,9);self.client.force_login(self.worker)
    def task(self,title,day,assignee=None,**kwargs):
        return Task.objects.create(title=title,start_date=day,due_date=kwargs.pop('due_date',day),assignee=assignee or self.emp,**kwargs)
    def post(self,**extra):
        return {'report_type':'weekly','period_start':self.week,'period_end':self.week+timedelta(days=6),'summary':'이번 주 실제 수행 결과','next_week_plan':'차주 변경된 계획','report_issues':'기술 지원 요청','reviewer':self.leader.pk,'revision':0,'action':'draft',**extra}
    def test_both_weeks_weekends_and_multiday_once(self):
        self.task('이번 주 업무',self.week);self.task('금요일',self.week+timedelta(days=4));self.task('토요일',self.week+timedelta(days=5));self.task('일요일',self.week+timedelta(days=6));self.task('차주 월요일',self.week+timedelta(days=7));self.task('차주 일요일',self.week+timedelta(days=13));self.task('범위 밖',self.week+timedelta(days=14));self.task('장기 업무',self.week-timedelta(days=3),due_date=self.week+timedelta(days=10))
        result=calendar_draft(self.worker,self.week)
        self.assertIn('토요일',result['current']);self.assertIn('일요일',result['current']);self.assertNotIn('차주 월요일',result['current'])
        self.assertIn('차주 월요일',result['next']);self.assertIn('차주 일요일',result['next']);self.assertNotIn('범위 밖',result['next'])
        self.assertEqual(result['current'].count('장기 업무'),1);self.assertEqual(result['next'].count('장기 업무'),1);self.assertTrue(result['weekend'])
    def test_only_own_performing_work_and_no_private_or_cancelled(self):
        self.task('동료 업무',self.week,assignee=self.peeremp);self.task('취소 업무',self.week,status='cancelled');self.task('초안 업무',self.week,status='draft')
        shared=self.task('협업 수행',self.week,assignee=self.other);TaskParticipation.objects.create(task=shared,employee=self.emp)
        result=calendar_draft(self.worker,self.week)
        self.assertEqual(result['task_ids'],[shared.pk]);self.assertNotIn('동료 업무',result['current'])
    def test_timed_exclusive_midnight_and_korea_timezone(self):
        tz=ZoneInfo('Asia/Seoul')
        self.task('이전 일요일만',self.week,activity_start=datetime(2026,11,8,20,tzinfo=tz),activity_end=datetime(2026,11,9,0,tzinfo=tz))
        self.task('이번 일요일',self.week,activity_start=datetime(2026,11,15,23,tzinfo=tz),activity_end=datetime(2026,11,16,0,tzinfo=tz))
        result=calendar_draft(self.worker,self.week);self.assertNotIn('이전 일요일만',result['current']);self.assertIn('이번 일요일',result['current']);self.assertNotIn('이번 일요일',result['next'])
    def test_get_prefills_without_saving_and_reviewer_defaults_to_leader(self):
        self.task('이번 주 자동 취합',self.week);self.task('다음 주 자동 취합',self.week+timedelta(days=7))
        response=self.client.get('/work/reports/weekly/new/',{'week':str(self.week+timedelta(days=4))})
        self.assertEqual(response.status_code,200);self.assertFalse(PeriodReport.objects.exists())
        form=response.context['form'];self.assertIn('이번 주 자동 취합',form['summary'].value());self.assertIn('다음 주 자동 취합',form['next_week_plan'].value());self.assertEqual(form['reviewer'].value(),self.leader.pk)
        self.assertEqual(form['period_start'].value(),self.week)

    def test_individual_and_period_get_never_seed_calendar_even_on_weekly_url(self):
        from unittest.mock import patch
        for kind in ['individual','period']:
            with patch('tasks.reports.calendar_draft') as draft:
                response=self.client.get('/work/reports/weekly/new/',{'week':str(self.week),'report_type':kind})
            self.assertEqual(response.status_code,200);draft.assert_not_called()
            self.assertFalse(response.context['form']['summary'].value())
            self.assertFalse(response.context['calendar_seed'])
            self.assertNotContains(response,'id="report-default-period"')
            if kind=='period':self.assertIsNone(response.context['form']['period_start'].value())

    def test_period_preview_is_explicit_owner_scoped_and_does_not_overwrite_or_save(self):
        own=self.task('기간 끝 업무',date(2026,12,1))
        self.task('다른 직원 비공개 업무',self.week,assignee=self.peeremp)
        self.task('취소 제외',self.week,status='cancelled')
        self.task('초안 제외',self.week,status='draft')
        response=self.client.post('/work/reports/new/',self.post(report_type='period',period_end=date(2026,12,2),summary='직접 작성 중인 내용',action='calendar'))
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.context['form']['summary'].value(),'직접 작성 중인 내용')
        self.assertIn('기간 끝 업무',response.context['calendar_preview']['current'])
        self.assertEqual(response.context['calendar_preview']['task_ids'],[own.pk])
        self.assertFalse(PeriodReport.objects.exists());self.assertFalse(ReportSubmission.objects.exists())
        self.assertContains(response,'이 내용으로 본문 채우기')

    def test_period_calendar_request_checks_kind_and_date_range(self):
        for extra in [{'report_type':'individual'},{'report_type':'period','period_end':self.week-timedelta(days=1)},{'report_type':'period','period_end':self.week+timedelta(days=367)}]:
            response=self.client.post('/work/reports/new/',self.post(action='calendar',**extra))
            self.assertTrue(response.context['calendar_error']);self.assertIsNone(response.context['calendar_preview'])
            self.assertFalse(PeriodReport.objects.exists())

    def test_nonweekly_submit_from_weekly_url_preserves_manual_body_without_calendar_flag(self):
        from unittest.mock import patch
        with patch('tasks.reports.calendar_draft') as draft:
            response=self.client.post('/work/reports/weekly/new/',self.post(report_type='individual',summary='직접 쓴 건별 보고',action='submit'))
        self.assertEqual(response.status_code,302);draft.assert_not_called()
        report=PeriodReport.objects.get();self.assertFalse(report.calendar_based)
        self.assertEqual(report.current_submission.snapshot['summary'],'직접 쓴 건별 보고')
    def test_manual_edits_survive_save_and_calendar_changes(self):
        task=self.task('원래 일정',self.week)
        self.client.post('/work/reports/weekly/new/',self.post(tasks=[task.pk]))
        report=PeriodReport.objects.get();self.assertEqual(report.summary,'이번 주 실제 수행 결과');self.assertEqual(report.next_week_plan,'차주 변경된 계획');self.assertTrue(report.calendar_based)
        task.title='바뀐 일정';task.save()
        response=self.client.get(f'/work/reports/{report.pk}/edit/');self.assertEqual(response.context['form']['summary'].value(),'이번 주 실제 수행 결과')
    def test_submission_both_sections_snapshot_and_review_return_resubmit(self):
        self.client.post('/work/reports/weekly/new/',self.post(action='submit'))
        report=PeriodReport.objects.get();first=report.current_submission;self.assertEqual(first.snapshot['next_week_plan'],'차주 변경된 계획');self.assertEqual(first.snapshot['report_issues'],'기술 지원 요청')
        self.assertEqual(self.client.post(f'/work/reports/{report.pk}/edit/',self.post()).status_code,403)
        self.client.force_login(self.leader);self.client.post(f'/work/reports/{report.pk}/',{'revision':report.revision,'decision':'returned','body':'차주 계획 보완'})
        report.refresh_from_db();self.client.force_login(self.worker)
        self.client.post(f'/work/reports/{report.pk}/edit/',self.post(revision=report.revision,action='submit',next_week_plan='보완한 계획'))
        report.refresh_from_db();self.assertEqual(report.current_submission.snapshot['next_week_plan'],'보완한 계획');first.refresh_from_db();self.assertEqual(first.snapshot['next_week_plan'],'차주 변경된 계획')
    def test_board_ignores_daily_other_week_and_draft(self):
        for kind,start,state in [('daily',self.week,'submitted'),('weekly',self.week-timedelta(days=7),'submitted'),('weekly',self.week,'draft')]:PeriodReport.objects.create(author=self.worker,reviewer=self.leader,report_type=kind,period_start=start,period_end=start+timedelta(days=6),summary='내용',state=state)
        row=next(x for x in submission_board(self.leader,self.week) if x['employee']==self.emp);self.assertEqual(row['label'],'미제출');self.assertIsNone(row['report'])
        self.client.post('/work/reports/weekly/new/',self.post(action='submit'))
        row=next(x for x in submission_board(self.leader,self.week) if x['employee']==self.emp);self.assertEqual(row['label'],'제출 완료')
    def test_board_team_scope_and_no_staff_board(self):
        rows=submission_board(self.leader,self.week);self.assertNotIn(self.other,[r['employee'] for r in rows]);self.assertEqual(submission_board(self.worker,self.week),[])
    def test_peer_cannot_read_report_or_draft(self):
        report=PeriodReport.objects.create(author=self.worker,reviewer=self.leader,period_start=self.week,period_end=self.week+timedelta(days=6),summary='비공개 초안')
        self.assertFalse(visible_reports(self.leader).filter(pk=report.pk).exists())
        report.state='submitted';report.save();self.client.force_login(self.peer)
        self.assertEqual(self.client.get(f'/work/reports/{report.pk}/').status_code,404)
        self.client.force_login(self.leader);self.assertEqual(self.client.get(f'/work/reports/{report.pk}/').status_code,200)
    def test_invalid_post_preserves_user_text(self):
        response=self.client.post('/work/reports/weekly/new/',self.post(reviewer=''))
        self.assertEqual(response.status_code,200);self.assertEqual(response.context['form']['summary'].value(),'이번 주 실제 수행 결과');self.assertFalse(PeriodReport.objects.exists())
    def test_board_render_and_no_friday_submission_restriction(self):
        self.client.post('/work/reports/weekly/new/',self.post(action='submit'));self.client.force_login(self.leader)
        response=self.client.get('/work/reports/',{'week':str(self.week+timedelta(days=4))})
        self.assertContains(response,'주간업무보고 제출 현황');self.assertContains(response,'제출 완료');self.assertEqual(response.context['start'],self.week)
    def test_collection_query_count_does_not_grow_with_tasks(self):
        for i in range(30):self.task('일정'+str(i),self.week)
        with self.assertNumQueries(2):calendar_draft(self.worker,self.week)
    def test_bad_week_falls_back_without_crash(self):
        self.assertEqual(week_start(date(9999,12,31)).weekday(),0)

    def test_support_request_visible_in_snapshot_when_no_next_plan(self):
        self.client.post('/work/reports/weekly/new/',self.post(action='submit',next_week_plan='',report_issues='지원 요청만 존재'))
        report=PeriodReport.objects.get();self.client.force_login(self.leader)
        response=self.client.get(f'/work/reports/{report.pk}/');self.assertContains(response,'지원 요청만 존재',count=2)

    def test_review_feedback_visible_to_sender_and_recipient_without_history_expansion(self):
        self.client.post('/work/reports/weekly/new/',self.post(action='submit'))
        report=PeriodReport.objects.get()
        self.client.force_login(self.leader)
        self.client.post(f'/work/reports/{report.pk}/',{'revision':report.revision,'decision':'returned','body':'교육 시간 보완\n<script>alert(1)</script>'})
        for user in [self.worker,self.leader]:
            self.client.force_login(user)
            response=self.client.get(f'/work/reports/{report.pk}/')
            html=response.content.decode()
            feedback=html.split('aria-labelledby="report-feedback-title">')[1].split('</section>')[0]
            self.assertIn('교육 시간 보완<br>',feedback)
            self.assertIn('보완 요청',feedback)
            self.assertIn('주간보고팀 · 팀장',feedback)
            self.assertNotIn('<details',feedback)
            self.assertIn('&lt;script&gt;',feedback)
            self.assertNotIn('<script>',feedback)
        self.client.force_login(self.outside)
        self.assertEqual(self.client.get(f'/work/reports/{report.pk}/').status_code,404)

    def test_previous_feedback_is_labelled_after_resubmission_and_latest_review_replaces_it(self):
        self.client.post('/work/reports/weekly/new/',self.post(action='submit'))
        report=PeriodReport.objects.get()
        self.client.force_login(self.leader)
        self.client.post(f'/work/reports/{report.pk}/',{'revision':report.revision,'decision':'returned','body':'차주 보완 의견'})
        report.refresh_from_db();self.client.force_login(self.worker)
        self.client.post(f'/work/reports/{report.pk}/edit/',self.post(revision=report.revision,action='submit'))
        report.refresh_from_db()
        self.assertContains(self.client.get(f'/work/reports/{report.pk}/'),'이전 제출본에 대한 피드백입니다.')
        self.client.force_login(self.leader)
        self.client.post(f'/work/reports/{report.pk}/',{'revision':report.revision,'decision':'approved','body':'최종 검토 의견'})
        response=self.client.get(f'/work/reports/{report.pk}/')
        feedback=response.content.decode().split('aria-labelledby="report-feedback-title">')[1].split('</section>')[0]
        self.assertIn('최종 검토 의견',feedback);self.assertIn('확인 완료',feedback);self.assertIn(f'제출 v{report.current_submission.version}',feedback)
        self.assertNotIn('차주 보완 의견',feedback)
        self.assertContains(response,'차주 보완 의견')

    def test_confirmation_accepts_missing_or_blank_feedback_but_return_requires_reason(self):
        for body in [None, '', '   ']:
            self.client.force_login(self.worker)
            self.client.post('/work/reports/weekly/new/',self.post(action='submit'))
            report=PeriodReport.objects.order_by('-pk').first()
            self.client.force_login(self.leader)
            for missing in ['', '   ']:
                response=self.client.post(f'/work/reports/{report.pk}/',{'revision':report.revision,'decision':'returned','body':missing})
                self.assertContains(response,'보완 요청 내용을 입력하세요.')
                report.refresh_from_db();self.assertEqual(report.state,'submitted')
                self.assertFalse(report.current_submission.reviews.exists())
            data={'revision':report.revision,'decision':'approved'}
            if body is not None:data['body']=body
            self.assertEqual(self.client.post(f'/work/reports/{report.pk}/',data).status_code,302)
            report.refresh_from_db();self.assertEqual(report.state,'approved')
            self.assertEqual(report.current_submission.reviews.get().body,'')
