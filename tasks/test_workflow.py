from datetime import timedelta, datetime, time
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from .models import Department, Employee, Task, TaskEvent
from .metrics import employee_metrics


class WorkflowTests(TestCase):
    def setUp(self):
        User=get_user_model()
        self.owner=User.objects.create_superuser('owner',password='test-password')
        self.member=User.objects.create_user('member',password='test-password')
        self.manager=User.objects.create_user('manager',password='test-password')
        self.peer=User.objects.create_user('peer',password='test-password')
        dept=Department.objects.get(name='보안기술팀')
        self.employee=Employee.objects.create(name='담당자',user=self.member,department=dept)
        self.leader=Employee.objects.create(name='부서장',user=self.manager,department=dept,role='manager')
        self.colleague=Employee.objects.create(name='동료',user=self.peer,department=dept)
        self.today=timezone.localdate()
        self.task=Task.objects.create(title='업무',assignee=self.employee,start_date=self.today-timedelta(days=5),due_date=self.today,assigned_at=timezone.now()-timedelta(days=5))
        self.client.force_login(self.member)

    def action(self, action, data, task=None):
        obj=task or self.task
        obj.refresh_from_db()
        if action in ['progress','submit'] and not obj.accepted_at and obj.assignee.user_id==int(self.client.session.get('_auth_user_id',0)):
            self.client.post(f'/tasks/{obj.pk}/accept/')
        return self.client.post(f'/tasks/{(task or self.task).pk}/actions/{action}/',data)

    def submit(self):
        self.assertEqual(self.action('submit',{'body':'작업 완료. 결과물: 내부 공유 문서'}).status_code,302)

    def approve(self,score=80):
        self.client.force_login(self.manager)
        return self.action('review',{'decision':'approve','quality_score':score,'body':'결과 확인. 요구사항 충족'})

    def test_progress_submit_approve_and_audit(self):
        self.assertEqual(self.action('progress',{'progress':40,'body':'분석 완료, 구현 예정','delay_reason':'외부 회신 대기'}).status_code,302)
        self.task.refresh_from_db()
        self.assertEqual(self.task.status,'blocked')
        self.assertEqual(self.task.progress,40)
        self.assertIsNotNone(self.task.last_report_at)
        self.submit()
        self.task.refresh_from_db()
        self.assertEqual(self.task.status,'review')
        self.assertEqual(self.task.deadline,'review')
        submitted=self.task.submitted_at
        self.assertEqual(self.approve().status_code,302)
        self.task.refresh_from_db()
        self.assertEqual(self.task.status,'done')
        self.assertEqual(self.task.completed_at,submitted)
        self.assertEqual(self.task.quality_score,80)
        self.assertEqual(set(self.task.events.values_list('kind',flat=True)),{'accepted','progress','submitted','approved'})
        approved=self.task.events.get(kind='approved')
        self.assertEqual(approved.author_name,'manager')
        self.assertEqual(approved.snapshot['quality_score'],'80')

    def test_return_and_resubmit_keep_original_result_and_feedback(self):
        self.submit()
        self.client.force_login(self.manager)
        self.assertEqual(self.action('review',{'decision':'return','body':'시험 결과 추가 필요'}).status_code,302)
        self.task.refresh_from_db()
        self.assertEqual(self.task.status,'doing')
        self.assertIsNone(self.task.submitted_at)
        self.client.force_login(self.member)
        self.assertContains(self.client.get('/dashboard/?scope=mine'),'시험 결과 추가 필요')
        self.assertEqual(self.task.events.filter(kind='submitted').count(),1)
        self.submit()
        self.assertEqual(self.task.events.filter(kind='submitted').count(),2)

    def test_no_self_approval_no_impersonated_reports(self):
        self.submit()
        self.assertEqual(self.action('review',{'decision':'approve','quality_score':100,'body':'직접 승인'}).status_code,403)
        self.client.force_login(self.manager)
        other=Task.objects.create(title='관리자 업무',assignee=self.leader,start_date=self.today,due_date=self.today)
        self.assertEqual(self.action('submit',{'body':'본인 완료'},other).status_code,302)
        self.assertEqual(self.action('review',{'decision':'approve','quality_score':100,'body':'직접 승인'},other).status_code,403)
        self.assertEqual(self.action('progress',{'progress':50,'body':'대신 보고'},self.task).status_code,403)
        self.client.force_login(self.owner)
        self.assertEqual(self.action('review',{'decision':'approve','quality_score':90,'body':'부서장 업무 검토 완료'},other).status_code,302)

    def test_peer_cannot_review_feedback_or_read_scores(self):
        self.submit()
        self.approve()
        self.client.force_login(self.peer)
        for action,data in [('review',{'decision':'approve','quality_score':100,'body':'무단 승인'}),('feedback',{'body':'무단 피드백'})]:
            self.assertEqual(self.action(action,data).status_code,403)
        r=self.client.get(f'/tasks/{self.task.pk}/')
        self.assertNotContains(r,'결과 평가')
        self.assertNotContains(r,'요구사항 충족')
        self.assertEqual(self.client.get('/dashboard/').context['rows'][0]['employee'],self.colleague)

    def test_form_validation_and_no_direct_done(self):
        for progress in [-1,100,101]:
            r=self.action('progress',{'progress':progress,'body':'보고','delay_reason':''})
            self.assertEqual(r.status_code,200)
            self.assertIn('progress',r.context['form'].errors)
        self.assertEqual(self.action('submit',{'body':''}).status_code,200)
        self.client.force_login(self.owner)
        data={'title':'업무','assignee':self.employee.pk,'status':'done','importance':'normal','priority':1,'start_date':self.task.start_date.isoformat(),'due_date':self.today.isoformat()}
        r=self.client.post(f'/tasks/{self.task.pk}/edit/',data)
        self.assertIn('status',r.context['form'].errors)
        self.task.refresh_from_db()
        self.assertEqual(self.task.status,'todo')

    def test_repeat_approval_and_closed_task_edit_blocked(self):
        self.submit()
        self.approve()
        events=self.task.events.count()
        r=self.approve()
        self.assertEqual(r.status_code,200)
        self.assertTrue(r.context['form'].non_field_errors())
        self.assertEqual(self.task.events.count(),events)
        self.assertEqual(self.client.get(f'/tasks/{self.task.pk}/edit/').status_code,403)
        self.client.force_login(self.member)
        self.assertEqual(self.action('progress',{'progress':20,'body':'완료 뒤 보고','delay_reason':''}).status_code,200)

    def test_rating_required_and_zero_is_valid(self):
        self.submit()
        self.client.force_login(self.manager)
        for score in ['',-1,101]:
            r=self.action('review',{'decision':'approve','quality_score':score,'body':'검토'})
            self.assertEqual(r.status_code,200)
            self.assertIn('quality_score',r.context['form'].errors)
        self.assertEqual(self.approve(0).status_code,302)
        self.task.refresh_from_db()
        self.assertEqual(self.task.quality_score,0)

    def test_deadline_change_requires_reason_and_keeps_baseline(self):
        self.client.force_login(self.manager)
        future=self.today+timedelta(days=7)
        data={'title':'업무','assignee':self.employee.pk,'status':'doing','importance':'normal','priority':1,'start_date':self.task.start_date.isoformat(),'due_date':future.isoformat()}
        r=self.client.post(f'/tasks/{self.task.pk}/edit/',data)
        self.assertIn('change_reason',r.context['form'].errors)
        data['change_reason']='고객 일정 변경'
        self.assertEqual(self.client.post(f'/tasks/{self.task.pk}/edit/',data).status_code,302)
        self.task.refresh_from_db()
        self.assertEqual(self.task.original_due_date,self.today)
        self.assertEqual(self.task.due_date,future)
        event=self.task.events.get(kind='changed')
        self.assertEqual(event.body,'고객 일정 변경')
        self.assertEqual(event.snapshot['before']['due_date'],str(self.today))
        self.client.force_login(self.member)
        data['due_date']=(future+timedelta(days=1)).isoformat()
        self.client.post(f'/tasks/{self.task.pk}/edit/',data)
        self.task.refresh_from_db()
        self.assertEqual(self.task.due_date,future)

    def test_dashboard_scope_and_unassigned_access(self):
        outside=Employee.objects.create(name='영업직원',department=Department.objects.get(name='영업팀'))
        other=Task.objects.create(title='타부서업무비밀',assignee=outside,start_date=self.today,due_date=self.today)
        self.assertNotContains(self.client.get('/dashboard/'),'타부서업무비밀')
        self.client.force_login(self.manager)
        self.assertTrue(self.client.get('/dashboard/').context['managing'])
        self.assertNotContains(self.client.get('/dashboard/'),'영업직원')
        self.assertEqual(self.client.get(f'/tasks/{other.pk}/actions/review/').status_code,404)
        self.client.force_login(self.owner)
        self.assertContains(self.client.get('/dashboard/'),'타부서업무비밀')
        self.assertContains(self.client.get('/dashboard/'),'부서장')
        unassigned=get_user_model().objects.create_user('unassigned')
        self.client.force_login(unassigned)
        self.assertEqual(self.client.get('/dashboard/').status_code,403)

    def test_report_alert_uses_employee_report_not_manager_feedback(self):
        self.assertTrue(self.task.needs_report)
        self.assertTrue(self.task.not_started)
        self.client.force_login(self.manager)
        self.action('feedback',{'body':'진행 확인 요청'})
        self.task.refresh_from_db()
        self.assertTrue(self.task.needs_report)
        self.client.force_login(self.member)
        self.action('progress',{'progress':30,'body':'오늘 수행 내용','delay_reason':''})
        self.task.refresh_from_db()
        self.assertFalse(self.task.needs_report)
        self.assertFalse(self.task.not_started)

    def test_admin_cannot_bypass_workflow(self):
        self.client.force_login(self.owner)
        r=self.client.post(f'/admin/tasks/task/{self.task.pk}/change/',{'status':'done','quality_score':100})
        self.assertEqual(r.status_code,403)
        self.assertEqual(self.client.post('/admin/tasks/task/add/',{}).status_code,403)
        self.task.refresh_from_db()
        self.assertEqual(self.task.status,'todo')


    def test_employee_feedback_recipient_only_and_manager_hierarchy(self):
        self.client.force_login(self.manager)
        path=f'/employees/{self.employee.pk}/feedback/'
        self.assertEqual(self.client.post(path,{'body':'직원 전용 개선 계획'}).status_code,302)
        self.client.force_login(self.member)
        self.assertContains(self.client.get('/dashboard/'),'직원 전용 개선 계획')
        self.assertEqual(self.client.post(path,{'body':'자가 피드백'}).status_code,403)
        self.client.force_login(self.peer)
        self.assertNotContains(self.client.get('/dashboard/'),'직원 전용 개선 계획')
        self.assertEqual(self.client.get(path).status_code,403)
        self.client.force_login(self.owner)
        self.assertEqual(self.client.post(f'/employees/{self.leader.pk}/feedback/',{'body':'부서장 개선 계획'}).status_code,302)
        self.client.force_login(self.member)
        self.assertNotContains(self.client.get('/dashboard/'),'부서장 개선 계획')
        self.client.force_login(self.manager)
        self.assertContains(self.client.get('/dashboard/?scope=mine'),'부서장 개선 계획')

    def test_manager_cannot_modify_or_assess_other_managers_or_owner(self):
        peer_user=get_user_model().objects.create_user('peer-manager')
        peer=Employee.objects.create(name='동급 관리자',user=peer_user,department=self.employee.department,role='manager')
        owner_profile=Employee.objects.create(name='최상위 관리자',user=self.owner,department=self.employee.department)
        self.client.force_login(self.manager)
        for employee in [peer,owner_profile]:
            task=Task.objects.create(title='상위 또는 동급 업무',assignee=employee,start_date=self.today,due_date=self.today)
            self.assertEqual(self.client.get(f'/tasks/{task.pk}/edit/').status_code,403)
            self.assertEqual(self.client.get(f'/employees/{employee.pk}/feedback/').status_code,403)
        ids=[row['employee'].pk for row in self.client.get('/dashboard/').context['rows']]
        self.assertNotIn(peer.pk,ids)
        self.assertNotIn(owner_profile.pk,ids)

    def test_attention_filter_and_employee_drilldown(self):
        Task.objects.create(title='추가 지연 업무',assignee=self.employee,start_date=self.today-timedelta(days=5),due_date=self.today-timedelta(days=1))
        self.client.force_login(self.manager)
        r=self.client.get('/dashboard/',{'attention':'1'})
        self.assertEqual([row['employee'] for row in r.context['rows']],[self.employee])
        self.assertEqual(r.context['attention_count'],1)
        self.assertContains(r,'전체 직원 보기')
        r=self.client.get('/dashboard/',{'employee':self.employee.pk})
        self.assertEqual(len(r.context['tasks']),2)

    def test_report_gap_is_recorded_with_report(self):
        self.action('progress',{'progress':20,'body':'첫 진행 보고','delay_reason':''})
        self.assertEqual(self.task.events.get(kind='progress').snapshot['report_gap_days'],5)


class MetricsTests(TestCase):
    def setUp(self):
        self.employee=Employee.objects.create(name='지표 대상',department=Department.objects.get(name='보안기술팀'))
        self.today=timezone.localdate()
        self.start=self.today-timedelta(days=30)
        self.end=self.today+timedelta(days=30)

    def make(self,**kwargs):
        values={'title':'지표 업무','assignee':self.employee,'start_date':self.today-timedelta(days=10),'due_date':self.today-timedelta(days=2)}
        values.update(kwargs)
        return Task.objects.create(**values)

    def metrics(self,tasks):
        return employee_metrics(self.employee,tasks,self.start,self.end)

    def test_weighted_score_and_legacy_pending_exclusion(self):
        ontime=timezone.make_aware(datetime.combine(self.today-timedelta(days=3),time(12)))
        complete=self.make(status='done',completed_at=ontime,quality_score=80)
        overdue=self.make()
        pending=self.make(status='review')
        legacy=self.make(status='done')
        row=self.metrics([complete,overdue,pending,legacy])
        self.assertEqual(row['eligible'],2)
        self.assertEqual(row['completion'],50)
        self.assertEqual(row['timeliness'],50)
        self.assertEqual(row['quality'],80)
        self.assertEqual(row['score'],59)
        self.assertEqual(row['legacy'],1)
        self.assertEqual(row['pending'],1)

    def test_no_invented_score_and_zero_quality(self):
        self.assertIsNone(self.metrics([])['score'])
        self.assertIsNone(self.metrics([self.make()])['score'])
        completed=self.make(status='done',completed_at=timezone.now(),quality_score=0)
        self.assertEqual(self.metrics([completed])['score'],30)

    def test_original_deadline_cannot_be_erased_and_attention(self):
        tasks=[self.make(),self.make()]
        for t in tasks:
            t.due_date=self.today+timedelta(days=10)
            t.save()
        row=self.metrics(tasks)
        self.assertEqual(row['late'],2)
        self.assertTrue(row['attention'])
        self.assertIn('기간 내 지연 2건',row['reasons'])
        self.assertEqual(row['timeliness'],0)

    def test_korea_date_boundary_and_review_delay(self):
        due=self.today-timedelta(days=2)
        submitted=timezone.make_aware(datetime.combine(due,time(23,59)))
        complete=self.make(status='done',completed_at=submitted,approved_at=timezone.now(),quality_score=100)
        row=self.metrics([complete])
        self.assertEqual(row['score'],100)
        self.assertEqual(row['on_time_count'],1)
        row=employee_metrics(self.employee,[complete],due+timedelta(days=1),self.end)
        self.assertIsNone(row['score'])

    def test_repeated_report_gaps_flag_attention(self):
        task=self.make()
        for _ in range(2):
            TaskEvent.objects.create(task=task,author_name='직원',kind='progress',body='보고',snapshot={'report_gap_days':4})
        row=self.metrics([task])
        self.assertEqual(row['report_gaps'],2)
        self.assertIn('기간 내 보고 공백 2회',row['reasons'])
        self.assertTrue(row['attention'])
