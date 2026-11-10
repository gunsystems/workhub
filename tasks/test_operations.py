from datetime import timedelta
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from .models import Employee, Department, Project, Task, WorkRequest, ChecklistItem
from .alerts import action_alerts


class OperationsTests(TestCase):
    def setUp(self):
        User=get_user_model()
        self.owner=User.objects.create_superuser('owner',password='test-password')
        self.manager=User.objects.create_user('manager',password='test-password')
        self.member=User.objects.create_user('member',password='test-password')
        self.peer=User.objects.create_user('peer',password='test-password')
        self.department=Department.objects.get(name='보안기술팀')
        self.sales=Department.objects.get(name='영업팀')
        self.leader=Employee.objects.create(name='팀장',department=self.department,user=self.manager,role='manager')
        self.employee=Employee.objects.create(name='직원',department=self.department,user=self.member)
        self.other=Employee.objects.create(name='동료',department=self.department,user=self.peer)
        self.outside=Employee.objects.create(name='외부서',department=self.sales)
        self.today=timezone.localdate()
        self.project=Project.objects.create(name='보안 구축 프로젝트',department=self.department,owner=self.leader,start_date=self.today-timedelta(days=10),due_date=self.today+timedelta(days=20))
        self.foreign_project=Project.objects.create(name='영업 비공개 프로젝트',department=self.sales,owner=self.outside,start_date=self.today,due_date=self.today+timedelta(days=20))
        self.task=Task.objects.create(title='프로젝트 업무',project=self.project,assignee=self.employee,start_date=self.today,due_date=self.today+timedelta(days=5))
        self.client.force_login(self.member)

    def task_data(self,**kwargs):
        data={'title':'배정 업무','description':'내용','project':self.project.pk,'assignee':self.employee.pk,'start_date':self.today.isoformat(),'due_date':(self.today+timedelta(days=5)).isoformat(),'status':'todo','importance':'high','priority':1,'report_days':2,'review_days':1}
        data.update(kwargs)
        return data

    def request(self,**kwargs):
        values={'title':'새로운 요청','description':'누락 방지 요청','department':self.department,'project':self.project,'requested_due_date':self.today+timedelta(days=5),'creator':self.member,'creator_name':'member'}
        values.update(kwargs)
        return WorkRequest.objects.create(**values)

    def test_project_rollup_scope_and_empty_warning(self):
        r=self.client.get('/projects/')
        self.assertContains(r,self.project.name)
        self.assertNotContains(r,self.foreign_project.name)
        self.assertEqual(self.client.get(f'/projects/{self.foreign_project.pk}/').status_code,404)
        r=self.client.get(f'/projects/{self.project.pk}/')
        self.assertEqual(r.context['summary']['total'],1)
        self.assertEqual(r.context['summary']['progress'],0)
        self.task.progress=70
        self.task.status='blocked'
        self.task.delay_reason='고객 승인 대기'
        self.task.save()
        r=self.client.get(f'/projects/{self.project.pk}/')
        self.assertEqual(r.context['summary']['progress'],70)
        self.assertEqual(r.context['summary']['blocked'],1)
        self.assertContains(r,'고객 승인 대기')
        self.client.force_login(self.owner)
        self.assertContains(self.client.get('/projects/'),'업무 미등록')

    def test_project_creation_and_editing_permissions(self):
        self.assertEqual(self.client.get('/projects/new/').status_code,403)
        self.client.force_login(self.manager)
        data={'name':'신규 프로젝트','department':self.department.pk,'owner':self.leader.pk,'description':'완료 기준','start_date':self.today.isoformat(),'due_date':(self.today+timedelta(days=10)).isoformat()}
        self.assertEqual(self.client.post('/projects/new/',data).status_code,302)
        data['department']=self.sales.pk
        r=self.client.post('/projects/new/',data)
        self.assertIn('department',r.context['form'].errors)
        data.update(department=self.department.pk,name=self.project.name,start_date=(self.today+timedelta(days=1)).isoformat())
        r=self.client.post(f'/projects/{self.project.pk}/edit/',data)
        self.assertIn('due_date',r.context['form'].errors)

    def test_cross_department_assignment_keeps_project_dates_validation(self):
        self.client.force_login(self.owner)
        r=self.client.post('/tasks/new/',self.task_data(project=self.foreign_project.pk))
        self.assertEqual(r.status_code,302)
        r=self.client.post('/tasks/new/',self.task_data(due_date=(self.today+timedelta(days=30)).isoformat()))
        self.assertIn('project',r.context['form'].errors)
        self.assertEqual(self.client.post('/tasks/new/',self.task_data()).status_code,302)

    def test_request_creation_and_personal_visibility(self):
        data={'title':'고객 요청 고유 접수명','description':'내용','department':self.department.pk,'project':self.project.pk,'requested_due_date':self.today.isoformat()}
        self.assertEqual(self.client.post('/intake/new/',data).status_code,302)
        intake=WorkRequest.objects.get(title='고객 요청 고유 접수명')
        self.assertEqual(intake.creator,self.member)
        self.client.force_login(self.peer)
        self.assertNotContains(self.client.get('/intake/'),'고객 요청 고유 접수명')
        self.assertEqual(self.client.get(f'/intake/{intake.pk}/').status_code,404)
        self.client.force_login(self.manager)
        self.assertContains(self.client.get('/intake/'),'고객 요청 고유 접수명')
        self.assertEqual(self.client.get(f'/intake/{intake.pk}/').status_code,200)

    def test_request_cannot_be_created_for_foreign_department_or_project(self):
        data={'title':'무단','description':'내용','department':self.sales.pk,'project':self.foreign_project.pk,'requested_due_date':self.today.isoformat()}
        r=self.client.post('/intake/new/',data)
        self.assertNotIn('department',r.context['form'].errors)
        self.assertIn('project',r.context['form'].errors)
        self.assertFalse(WorkRequest.objects.exists())

    def test_request_assignment_is_atomic_scoped_and_idempotent(self):
        intake=self.request()
        path=f'/intake/{intake.pk}/assign/'
        self.assertEqual(self.client.post(path,self.task_data()).status_code,403)
        self.client.force_login(self.manager)
        self.assertEqual(self.client.post(path,self.task_data()).status_code,302)
        intake.refresh_from_db()
        self.assertIsNotNone(intake.task_id)
        self.assertIsNotNone(intake.assigned_at)
        self.assertEqual(intake.task.assignee,self.employee)
        self.assertEqual(intake.task.events.get().snapshot['request_id'],intake.pk)
        count=Task.objects.count()
        self.assertEqual(self.client.post(path,self.task_data()).status_code,302)
        self.assertEqual(Task.objects.count(),count)
        foreign=self.request(department=self.sales,project=self.foreign_project)
        self.assertEqual(self.client.get(f'/intake/{foreign.pk}/assign/').status_code,404)
        other=self.request()
        r=self.client.post(f'/intake/{other.pk}/assign/',self.task_data(assignee=self.outside.pk))
        self.assertIn('assignee',r.context['form'].errors)

    def test_request_cancellation_requires_reason_and_retains_record(self):
        intake=self.request()
        url=f'/intake/{intake.pk}/close/'
        self.assertEqual(self.client.post(url,{}).status_code,200)
        self.assertEqual(self.client.post(url,{'reason':'중복 접수'}).status_code,302)
        intake.refresh_from_db()
        self.assertEqual(intake.state,'취소')
        self.assertIn('중복 접수',intake.close_reason)
        self.client.force_login(self.manager)
        self.assertEqual(self.client.post(f'/intake/{intake.pk}/assign/',self.task_data()).status_code,403)

    def test_acceptance_owner_only_post_only_and_no_duplicate_history(self):
        path=f'/tasks/{self.task.pk}/accept/'
        self.assertEqual(self.client.get(path).status_code,405)
        self.client.force_login(self.manager)
        self.assertEqual(self.client.post(path).status_code,403)
        self.client.force_login(self.member)
        for _ in range(2): self.assertEqual(self.client.post(path).status_code,302)
        self.task.refresh_from_db()
        self.assertIsNotNone(self.task.accepted_at)
        self.assertEqual(self.task.events.filter(kind='accepted').count(),1)

    def test_reassignment_requires_new_acceptance_and_resets_report_baseline(self):
        self.task.accepted_at=timezone.now()
        self.task.last_report_at=timezone.now()-timedelta(days=10)
        self.task.save()
        self.client.force_login(self.manager)
        data=self.task_data(assignee=self.other.pk)
        self.assertEqual(self.client.post(f'/tasks/{self.task.pk}/edit/',data).status_code,302)
        self.task.refresh_from_db()
        self.assertIsNone(self.task.accepted_at)
        self.assertIsNone(self.task.last_report_at)
        self.assertFalse(self.task.needs_report)
        self.assertTrue(self.task.awaiting_acceptance)

    def test_required_checklist_gates_completion_and_records_action(self):
        item=ChecklistItem.objects.create(task=self.task,title='결과물 확인',required=True)
        optional=ChecklistItem.objects.create(task=self.task,title='선택 확인',required=False)
        submit=f'/tasks/{self.task.pk}/actions/submit/'
        r=self.client.post(submit,{'body':'완료했습니다'})
        self.assertTrue(r.context['form'].non_field_errors())
        self.task.refresh_from_db()
        self.assertEqual(self.task.status,'todo')
        self.assertFalse(self.task.events.exists())
        self.assertEqual(self.client.post(f'/tasks/{self.task.pk}/checklist/{item.pk}/',{'action':'check'}).status_code,302)
        self.assertEqual(self.client.post(submit,{'body':'필수 결과물 완료'}).status_code,302)
        self.assertEqual(self.client.post(f'/tasks/{self.task.pk}/checklist/{item.pk}/',{'action':'uncheck'}).status_code,403)
        self.assertEqual(self.task.events.filter(kind='checklist').count(),1)

    def test_checklist_cross_task_and_retirement_permissions(self):
        item=ChecklistItem.objects.create(task=self.task,title='필수 확인')
        other=Task.objects.create(title='다른 업무',assignee=self.employee,start_date=self.today,due_date=self.today)
        self.assertEqual(self.client.post(f'/tasks/{other.pk}/checklist/{item.pk}/',{'action':'check'}).status_code,404)
        self.assertEqual(self.client.post(f'/tasks/{self.task.pk}/checklist/{item.pk}/',{'action':'retire'}).status_code,403)
        self.client.force_login(self.manager)
        self.assertEqual(self.client.post(f'/tasks/{self.task.pk}/checklist/{item.pk}/',{'action':'check'}).status_code,403)
        self.assertEqual(self.client.post(f'/tasks/{self.task.pk}/checklist/{item.pk}/',{'action':'retire'}).status_code,302)
        item.refresh_from_db()
        self.assertTrue(item.retired)
        self.assertFalse(item.checked)

    def test_alerts_conditions_and_permissions(self):
        self.assertIn(f'accept:{self.task.pk}',{a['key'] for a in action_alerts(self.member)})
        self.assertNotIn(f'accept:{self.task.pk}',{a['key'] for a in action_alerts(self.peer)})
        self.task.assigned_at=timezone.now()-timedelta(days=5)
        self.task.start_date=self.today-timedelta(days=5)
        self.task.report_days=2
        self.task.save()
        self.assertTrue(self.task.needs_report)
        self.assertIn(f'report:{self.task.pk}',{a['key'] for a in action_alerts(self.member)})
        self.task.status='review'
        self.task.submitted_at=timezone.now()-timedelta(days=3)
        self.task.save()
        self.assertTrue(self.task.review_overdue)
        alerts=action_alerts(self.manager)
        self.assertIn(f'review:{self.task.pk}',{a['key'] for a in alerts})
        self.assertNotIn(f'report:{self.task.pk}',{a['key'] for a in alerts})
        intake=self.request(created_at=timezone.now()-timedelta(days=3))
        self.assertIn(f'intake:{intake.pk}',{a['key'] for a in action_alerts(self.manager)})
        self.client.force_login(self.manager)
        self.assertContains(self.client.get('/alerts/'),'검토 기한 초과')

    def test_unlinked_accounts_cannot_use_operations(self):
        user=get_user_model().objects.create_user('unlinked')
        self.client.force_login(user)
        for path in ['/projects/','/intake/','/intake/new/','/alerts/']:
            self.assertEqual(self.client.get(path).status_code,403)

    def test_health_identifies_release_without_exposing_accounts(self):
        self.client.logout()
        r=self.client.get('/healthz/')
        self.assertEqual(r.status_code,200)
        from tasks.version import VERSION
        self.assertEqual(r.json()['version'],VERSION)
        self.assertEqual(r.json()['status'],'ok')
        self.assertNotContains(r,'owner')

    def test_missing_login_alert_is_grouped_by_employee(self):
        employee=Employee.objects.create(name='계정 미연결',department=self.department)
        for index in range(2):
            Task.objects.create(title=f'미연결 업무 {index}',assignee=employee,start_date=self.today,due_date=self.today)
        account_alerts=[a for a in action_alerts(self.owner) if a['key']==f'account:{employee.pk}']
        self.assertEqual(len(account_alerts),1)

    def test_guide_has_role_appropriate_setup_and_requires_login(self):
        self.assertContains(self.client.get('/guide/'),'업무 요청 접수')
        self.assertNotContains(self.client.get('/guide/'),'먼저 테스트 계정을 연결하세요')
        self.client.force_login(self.owner)
        self.assertContains(self.client.get('/guide/'),'먼저 테스트 계정을 연결하세요')
        self.client.logout()
        self.assertEqual(self.client.get('/guide/').status_code,302)
