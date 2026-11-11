from datetime import date, timedelta
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone
from .models import Department, Employee, Task, WorkCategory, PeriodReport, ReportSubmission
from .report_grouping import grouped_draft


@override_settings(WORKHUB_TEAM_REPORT_AGGREGATION=True, WORKHUB_REQUIRE_HIWORKS=False, WORKHUB_SCHEDULER_ENABLED=False,
                   PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class ReportGroupingTests(TestCase):
    def setUp(self):
        self.team = Department.objects.create(name='취합팀')
        self.other_team = Department.objects.create(name='다른팀')
        U = get_user_model()
        self.leader = U.objects.create_user('group_leader')
        self.worker = U.objects.create_user('group_worker')
        self.peer = U.objects.create_user('group_peer')
        self.head = U.objects.create_user('group_head')
        self.outside = U.objects.create_user('group_outside')
        for user, name, position, team in [(self.leader,'팀장','leader',self.team),
                (self.worker,'직원 A','member',self.team),(self.peer,'직원 B','member',self.team),
                (self.head,'본부장','head',self.team),(self.outside,'타팀 직원','member',self.other_team)]:
            Employee.objects.create(user=user,name=name,position=position,department=team)
        self.start = date(2026,11,9)
        self.end = self.start + timedelta(days=6)
        self.client.force_login(self.leader)

    def source(self, user=None, summary='진행 결과', plan='', issues='', tasks=None, week=None, version=1):
        start = week or self.start
        report = PeriodReport.objects.create(author=user or self.worker,reviewer=self.leader,
            report_type='weekly',period_start=start,period_end=start+timedelta(days=6),summary=summary,
            next_week_plan=plan,state='submitted',submitted_at=timezone.now())
        sub = ReportSubmission.objects.create(report=report,version=version,
            snapshot={'summary':summary,'next_week_plan':plan,'report_issues':issues,'tasks':tasks or []})
        report.current_submission=sub;report.save()
        return sub

    def task(self, title, **extra):
        return Task.objects.create(title=title,assignee=self.worker.employee,start_date=self.start,due_date=self.end,**extra)

    def payload(self, sources, **extra):
        return {'report_type':'weekly','period_start':str(self.start),'period_end':str(self.end),
                'sources':[s.pk for s in sources],'summary':'이미 직접 쓴 내용','next_week_plan':'수정한 계획',
                'report_issues':'수동 이슈','reviewer':self.head.pk,'revision':0,'action':'group',**extra}

    def test_groups_explicit_relationships_and_preserves_every_kind_of_text(self):
        a = self.source(summary='- [고객사: 고객 A] [기술지원] 지원 완료\n- [벤더사: 벤더 B] 문의 회신\n- [교육] 기술교육\n추가로 적은 새 업무',
                        plan='[고객사: 고객 A] 차주 지원',issues='[고객사: 고객 A] 장애 이슈')
        b = self.source(self.peer,summary='[고객사: 고객 A] 다른 진행 보고')
        draft = grouped_draft(self.leader,[a,b])
        for value in ['【고객사 · 고객 A】','【벤더사 · 벤더 B】','【팀 운영·기타】','【미분류 · 확인 필요】',
                      '지원 완료','문의 회신','기술교육','추가로 적은 새 업무']:
            self.assertIn(value,draft['current'])
        self.assertEqual(draft['current'].count('【고객사 · 고객 A】'),1)
        self.assertIn('차주 지원',draft['next']);self.assertIn('장애 이슈',draft['issues'])

    def test_only_identical_lines_merge_and_all_contributors_remain(self):
        a=self.source(summary='[고객사: ACME] 진행 30%')
        b=self.source(self.peer,summary='[고객사: ACME] 진행 30%\n[고객사: ACME] 진행 50%')
        result=grouped_draft(self.leader,[a,b])['current']
        self.assertEqual(result.count('진행 30%'),1);self.assertIn('진행 50%',result)
        self.assertNotIn('↳ 출처:',result)
        self.assertIn('직원 A',' '.join(grouped_draft(self.leader,[a,b])['source_labels']))
        self.assertIn('직원 B',' '.join(grouped_draft(self.leader,[a,b])['source_labels']))

    def test_frozen_metadata_wins_and_old_snapshots_can_use_visible_current_labels(self):
        task=self.task('고객 점검',customer='현재 고객')
        old=self.source(summary='고객 점검 결과',tasks=[{'id':task.pk,'title':task.title}])
        result=grouped_draft(self.leader,[old]);self.assertIn('【고객사 · 현재 고객】',result['current']);self.assertTrue(result['supplemented'])
        old.snapshot['tasks'][0].update(customer='제출 당시 고객',report_group='customer')
        old.save();result=grouped_draft(self.leader,[old])
        self.assertIn('【고객사 · 제출 당시 고객】',result['current']);self.assertNotIn('현재 고객',result['current'])

    def test_does_not_guess_company_from_prose_or_use_ambiguous_task_match(self):
        source=self.source(summary='ACME 문의 완료',tasks=[{'title':'문의','customer':'A'}, {'title':'문의 완료','vendor':'B'}])
        result=grouped_draft(self.leader,[source])
        self.assertIn('【미분류 · 확인 필요】',result['current']);self.assertIn('ACME 문의 완료',result['current'])

    def test_head_regroups_edited_team_report_without_reverting_to_original(self):
        member=self.source(summary='[고객사: A사] 직원 원문')
        first=grouped_draft(self.leader,[member])
        team=self.source(self.leader,summary=first['current'].replace('직원 원문','팀장이 수정한 실적'))
        team.report.reviewer=self.head;team.report.save()
        result=grouped_draft(self.head,[team])['current']
        self.assertEqual(result.count('【고객사 · A사】'),1)
        self.assertIn('팀장이 수정한 실적',result);self.assertNotIn('직원 원문',result)
        self.assertNotIn('↳ 출처:',result)
        self.assertIn('팀장',' '.join(grouped_draft(self.head,[team])['source_labels']))

    def test_team_get_prefills_latest_eligible_sources_without_saving(self):
        current=self.source(summary='[벤더사: B사] 문의 완료')
        self.source(summary='다른 주',week=self.start-timedelta(days=7))
        old=self.source(self.peer,summary='이전 제출본');old.report.state='returned';old.report.save()
        before=(PeriodReport.objects.count(),ReportSubmission.objects.count())
        response=self.client.get('/work/reports/team/new/',{'week':str(self.start)})
        self.assertEqual(response.status_code,200)
        self.assertIn('【벤더사 · B사】',response.context['form']['summary'].value())
        self.assertEqual(response.context['form']['sources'].value(),[current.pk])
        self.assertEqual(before,(PeriodReport.objects.count(),ReportSubmission.objects.count()))
        self.assertEqual(response.context['form']['reviewer'].value(),self.head.pk)

    def test_group_preview_keeps_manual_fields_and_no_report_written(self):
        sub=self.source(summary='[고객사: A사] 취합할 내용')
        before=PeriodReport.objects.count()
        response=self.client.post('/work/reports/team/new/',self.payload([sub]))
        self.assertContains(response,'분류 초안 미리보기')
        self.assertEqual(response.context['form']['summary'].value(),'이미 직접 쓴 내용')
        self.assertIn('취합할 내용',response.context['group_preview']['current'])
        self.assertEqual(PeriodReport.objects.count(),before)

    def test_grouping_rejects_wrong_week_duplicate_author_and_foreign_sources(self):
        own=self.source();duplicate=self.source();other_week=self.source(self.peer,week=self.start-timedelta(days=7))
        foreign=self.source(self.outside,summary='타팀 비밀')
        foreign.report.reviewer=self.head;foreign.report.save()
        for selected,message in [([own,duplicate],'한 개만 선택'),([other_week],'같은 주간 보고 기간'),([foreign],'조회할 수 없습니다')]:
            response=self.client.post('/work/reports/team/new/',self.payload(selected))
            self.assertIn(message,response.context['group_error'])
            self.assertIsNone(response.context.get('group_preview'))
            if selected==[foreign]:self.assertNotContains(response,'타팀 비밀')

    def test_worker_cannot_open_or_post_grouping(self):
        sub=self.source()
        self.client.force_login(self.worker)
        self.assertEqual(self.client.get('/work/reports/team/new/').status_code,403)
        self.assertEqual(self.client.post('/work/reports/new/',self.payload([sub])).status_code,403)

    def test_final_submission_preserves_manual_edits_and_selected_original_versions(self):
        sub=self.source(summary='[고객사: A사] 원문')
        response=self.client.post('/work/reports/team/new/',self.payload([sub],action='submit'))
        self.assertEqual(response.status_code,302)
        report=PeriodReport.objects.get(author=self.leader)
        self.assertEqual(report.current_submission.snapshot['summary'],'이미 직접 쓴 내용')
        self.assertEqual(report.current_submission.snapshot['source_submission_ids'],[sub.pk])
        newer=ReportSubmission.objects.create(report=sub.report,version=2,snapshot={'summary':'새 제출 내용'})
        sub.report.current_submission=newer;sub.report.save()
        self.assertEqual(report.current_submission.snapshot['source_submission_ids'],[sub.pk])
        self.client.force_login(self.head)
        response=self.client.get(f'/work/reports/{report.pk}/')
        self.assertContains(response,'원문');self.assertNotContains(response,'새 제출 내용')

    def test_group_preview_escapes_source_markup(self):
        sub=self.source(summary='[고객사: <script>악성</script>] <img src=x onerror=alert(1)>')
        response=self.client.post('/work/reports/team/new/',self.payload([sub]))
        self.assertContains(response,'&lt;img');self.assertNotContains(response,'<img src=x')

    def test_task_relationship_metadata_frozen_at_submission(self):
        task=self.task('벤더 문의',vendor='벤더 A',solution='제품 X',report_group='vendor')
        self.client.force_login(self.worker)
        response=self.client.post('/work/reports/new/',self.payload([],action='submit',tasks=[task.pk],reviewer=self.leader.pk))
        self.assertEqual(response.status_code,302)
        frozen=PeriodReport.objects.get(author=self.worker).current_submission.snapshot['tasks'][0]
        self.assertEqual(frozen['vendor'],'벤더 A');self.assertEqual(frozen['solution'],'제품 X')
        task.vendor='바뀐 이름';task.save();self.assertEqual(frozen['vendor'],'벤더 A')

    def test_existing_task_editor_keeps_new_labels_when_old_form_has_no_fields(self):
        from .forms import TaskForm
        task=self.task('고객 지원',customer='고객 A',vendor='벤더 B',solution='제품 C',report_group='vendor')
        data={'title':task.title,'assignee':self.worker.employee.pk,'status':'todo','importance':'normal','priority':3,'start_date':str(self.start),'due_date':str(self.end),'customer':'고객 A'}
        form=TaskForm(data,instance=task,user=self.worker)
        self.assertTrue(form.is_valid(),form.errors)
        saved=form.save();self.assertEqual(saved.vendor,'벤더 B');self.assertEqual(saved.report_group,'vendor')

    def test_explicit_general_and_manager_reclassification_override_original_tags(self):
        source=self.source(summary='[팀 운영·기타] [고객사: A사] 내부 준비 회의')
        self.assertIn('【팀 운영·기타】',grouped_draft(self.leader,[source])['current'])
        source.snapshot['summary']='【벤더사 · B사】\n- [고객사: A사] 벤더 협의로 재분류'
        self.assertIn('【벤더사 · B사】',grouped_draft(self.leader,[source])['current'])
        self.assertNotIn('【고객사 · A사】',grouped_draft(self.leader,[source])['current'])

    def test_old_version_insert_without_new_columns_uses_database_defaults(self):
        from django.db import connection
        self.assertEqual(Task().vendor,'');self.assertEqual(Task().solution,'')
        task=self.task('이전 버전 등록 호환')
        fields=[field.column for field in Task._meta.local_fields if field.column not in ['id','vendor','solution','report_group']]
        columns=','.join(connection.ops.quote_name(name) for name in fields)
        table=connection.ops.quote_name(Task._meta.db_table)
        with connection.cursor() as cursor:
            cursor.execute(f'INSERT INTO {table} ({columns}) SELECT {columns} FROM {table} WHERE id=%s',[task.pk])
            new_id=cursor.lastrowid
        legacy=Task.objects.get(pk=new_id)
        self.assertEqual(legacy.report_group,'auto');self.assertEqual(legacy.vendor,'');self.assertEqual(legacy.solution,'')
