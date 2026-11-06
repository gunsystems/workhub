from datetime import timedelta
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase
from django.utils import timezone
from .models import Department, Employee, Task


class DepartmentAccessTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.admin = User.objects.create_superuser('owner',password='test-password')
        self.security = Department.objects.get_or_create(name='보안기술팀')[0]
        self.sales = Department.objects.get_or_create(name='영업팀')[0]
        self.member = User.objects.create_user('security',password='test-password')
        self.manager = User.objects.create_user('manager',password='test-password')
        self.unassigned = User.objects.create_user('unassigned',password='test-password')
        self.employee = Employee.objects.create(name='보안 담당',department=self.security,user=self.member)
        self.leader = Employee.objects.create(name='보안 관리자',department=self.security,user=self.manager,role='manager')
        self.other = Employee.objects.create(name='타부서비공개직원',department=self.sales)
        today = timezone.localdate()
        self.own = Task.objects.create(title='본인 업무',assignee=self.employee,start_date=today-timedelta(days=2),due_date=today)
        self.team = Task.objects.create(title='팀장 업무',assignee=self.leader,start_date=today,due_date=today)
        self.foreign = Task.objects.create(title='타부서비공개업무',assignee=self.other,start_date=today-timedelta(days=2),due_date=today-timedelta(days=1))
        self.client.force_login(self.member)

    def data(self, assignee=None):
        today = timezone.localdate().isoformat()
        return dict(title='등록 업무',description='설명',assignee=(assignee or self.employee).pk,status='todo',importance='normal',priority=2,start_date=today,due_date=today)

    def test_department_calendar_filters_and_alerts_do_not_leak(self):
        for mode in ['month','week','day']:
            r=self.client.get('/',{'view':mode})
            self.assertContains(r,'본인 업무')
            self.assertContains(r,'팀장 업무')
            self.assertNotContains(r,'타부서비공개')
            self.assertEqual(r.context['count'],2)
            self.assertEqual(len(r.context['overdue']),0)
            self.assertEqual(list(r.context['departments']),[self.security])
            self.assertEqual(set(r.context['employees']),{self.employee,self.leader})
        r=self.client.get('/',{'department':self.sales.pk,'employee':self.other.pk})
        self.assertEqual(r.context['count'],0)
        self.assertNotContains(r,'타부서비공개')

    def test_foreign_task_detail_edit_get_and_post_are_blocked(self):
        for user in [self.member,self.manager]:
            self.client.force_login(user)
            for path in [f'/tasks/{self.foreign.pk}/',f'/tasks/{self.foreign.pk}/edit/']:
                self.assertEqual(self.client.get(path).status_code,404)
            self.assertEqual(self.client.post(f'/tasks/{self.foreign.pk}/edit/',self.data()).status_code,404)
        self.foreign.refresh_from_db()
        self.assertEqual(self.foreign.assignee,self.other)

    def test_member_can_edit_own_but_only_read_colleagues(self):
        self.assertContains(self.client.get(f'/tasks/{self.own.pk}/'),'업무 수정')
        self.assertNotContains(self.client.get(f'/tasks/{self.team.pk}/'),'업무 수정')
        self.assertEqual(self.client.post(f'/tasks/{self.own.pk}/edit/',self.data()).status_code,302)
        self.assertEqual(self.client.get(f'/tasks/{self.team.pk}/edit/').status_code,403)
        self.assertEqual(self.client.post(f'/tasks/{self.team.pk}/edit/',self.data()).status_code,403)
        self.team.refresh_from_db()
        self.assertEqual(self.team.title,'팀장 업무')

    def test_forged_assignee_is_rejected_on_create_and_edit(self):
        for user,targets in [(self.member,[self.leader,self.other]),(self.manager,[self.other])]:
            self.client.force_login(user)
            for target in targets:
                for path in ['/tasks/new/',f'/tasks/{self.own.pk}/edit/']:
                    r=self.client.post(path,self.data(target))
                    self.assertEqual(r.status_code,200)
                    self.assertIn('assignee',r.context['form'].errors)
        self.assertEqual(Task.objects.count(),3)
        self.own.refresh_from_db()
        self.assertEqual(self.own.assignee,self.employee)

    def test_manager_can_assign_and_edit_in_own_department(self):
        self.client.force_login(self.manager)
        r=self.client.get('/tasks/new/')
        self.assertEqual(set(r.context['form'].fields['assignee'].queryset),{self.employee,self.leader})
        self.assertEqual(self.client.post('/tasks/new/',self.data(self.employee)).status_code,302)
        self.assertEqual(self.client.post(f'/tasks/{self.own.pk}/edit/',self.data(self.leader)).status_code,302)
        self.own.refresh_from_db()
        self.assertEqual(self.own.assignee,self.leader)

    def test_superuser_has_company_wide_access(self):
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get('/').context['count'],3)
        self.assertEqual(self.client.get(f'/tasks/{self.foreign.pk}/').status_code,200)
        self.assertEqual(self.client.post(f'/tasks/{self.foreign.pk}/edit/',self.data(self.employee)).status_code,302)
        self.assertEqual(self.client.get('/admin/tasks/employee/').status_code,200)

    def test_missing_employee_profile_fails_closed(self):
        self.client.force_login(self.unassigned)
        for path in ['/','/tasks/new/']:
            self.assertEqual(self.client.get(path).status_code,403)
        self.assertEqual(self.client.post('/tasks/new/',self.data()).status_code,403)
        self.assertEqual(self.client.get(f'/tasks/{self.own.pk}/').status_code,404)

    def test_staff_and_django_permissions_cannot_bypass_scope(self):
        self.member.is_staff=True
        self.member.save()
        self.member.user_permissions.set(Permission.objects.all())
        for path in ['/admin/','/admin/tasks/task/','/admin/tasks/employee/','/admin/auth/user/']:
            self.assertEqual(self.client.get(path).status_code,302)
        self.assertEqual(self.client.get(f'/tasks/{self.foreign.pk}/').status_code,404)
        self.assertNotContains(self.client.get('/'),'직원 · 부서 관리')

    def test_department_and_role_changes_apply_immediately(self):
        self.employee.department=self.sales
        self.employee.save()
        r=self.client.get('/')
        self.assertEqual(r.context['count'],2)
        self.assertNotContains(r,'팀장 업무')
        self.assertEqual(self.client.get(f'/tasks/{self.team.pk}/').status_code,404)
        self.employee.user=None
        self.employee.save()
        self.assertEqual(self.client.get('/').status_code,403)

    def test_department_setup_preserves_real_employees_and_is_repeatable(self):
        from importlib import import_module
        from types import SimpleNamespace
        from django.apps import apps
        setup = import_module('tasks.migrations.0003_initial_departments').prepare_departments
        old = Department.objects.create(name='기술지원팀')
        real = Employee.objects.create(name='실제 직원',department=old)
        demo = Employee.objects.create(name='데모 (예시)',department=old)
        editor = SimpleNamespace(connection=SimpleNamespace(alias='default'))
        setup(apps,editor)
        setup(apps,editor)
        real.refresh_from_db()
        demo.refresh_from_db()
        self.assertEqual(real.department,old)
        self.assertEqual(demo.department,self.security)
        self.assertEqual(Department.objects.filter(name='보안기술팀').count(),1)
