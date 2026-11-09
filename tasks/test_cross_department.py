from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone
from .models import Department, Employee, Project, Task
from .forms import TaskForm


@override_settings(WORKHUB_REQUIRE_HIWORKS=False,WORKHUB_SCHEDULER_ENABLED=False,
                   PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class CrossDepartmentAssignmentTests(TestCase):
    def setUp(self):
        U=get_user_model();self.root=U.objects.create_superuser('operator')
        self.management=Department.objects.get_or_create(name='경영관리')[0];self.sales=Department.objects.get_or_create(name='영업팀')[0]
        self.worker=U.objects.create_user('sales-worker')
        self.emp=Employee.objects.create(name='수행 직원',user=self.worker,department=self.sales)
        self.owner=Employee.objects.create(name='주관 담당자',department=self.management)
        self.day=timezone.localdate()
        self.project=Project.objects.create(name='부서 협업',owner=self.owner,department=self.management,start_date=self.day,due_date=self.day)

    def data(self,**kwargs):
        return {'title':'다른 팀 배정','assignee':self.emp.pk,'project':self.project.pk,
                'start_date':self.day,'due_date':self.day,'status':'todo','importance':'normal',
                'priority':3,'allocation':'shared','revision':0,'management_mode':'event',**kwargs}

    def test_root_can_assign_project_work_across_departments(self):
        self.client.force_login(self.root)
        self.assertEqual(self.client.post('/work/new/',self.data()).status_code,302)
        task=Task.objects.get();self.assertEqual(task.project_id,self.project.pk);self.assertEqual(task.assignee_id,self.emp.pk)

    def test_representative_and_head_can_assign_across_departments_without_system_admin(self):
        for position in ['executive','head']:
            u=get_user_model().objects.create_user(position)
            Employee.objects.create(name=position,user=u,department=self.management,position=position)
            self.client.force_login(u)
            response=self.client.post('/work/new/',self.data(title=position))
            self.assertEqual(response.status_code,302)
            task=Task.objects.get(title=position)
            self.assertEqual(task.assignee_id,self.emp.pk);self.assertFalse(u.is_staff)

    def test_other_team_project_not_selectable_by_unrelated_regular_employee(self):
        form=TaskForm(self.data(),user=self.worker)
        self.assertFalse(form.is_valid());self.assertIn('project',form.errors)

    def test_assigned_employee_can_read_own_work_without_all_project_work(self):
        self.client.force_login(self.root);self.client.post('/work/new/',self.data())
        own=Task.objects.get()
        unrelated=Task.objects.create(title='다른 직원 업무',assignee=self.owner,project=self.project,start_date=self.day,due_date=self.day,workflow_version=2,creator=self.root)
        self.client.force_login(self.worker)
        self.assertEqual(self.client.get(f'/tasks/{own.pk}/').status_code,200)
        self.assertEqual(self.client.get(f'/tasks/{unrelated.pk}/').status_code,404)
        page=self.client.get(f'/projects/{self.project.pk}/')
        self.assertEqual(page.status_code,200);self.assertNotContains(page,'다른 직원 업무')

    def test_regular_participant_can_register_cross_team_collaboration(self):
        self.client.force_login(self.root);self.client.post('/work/new/',self.data())
        engineer=get_user_model().objects.create_user('engineer')
        engineering=Department.objects.get_or_create(name='엔지니어팀')[0]
        colleague=Employee.objects.create(name='엔지니어',user=engineer,department=engineering)
        self.client.force_login(self.worker)
        response=self.client.post('/work/new/',self.data(title='영업 기술 협업',collaborators=[colleague.pk]))
        self.assertEqual(response.status_code,302)
        task=Task.objects.get(title='영업 기술 협업')
        self.assertEqual(set(task.participants.values_list('pk',flat=True)),{self.emp.pk,colleague.pk})
        self.client.force_login(engineer)
        self.assertEqual(self.client.get(f'/projects/{self.project.pk}/').status_code,200)
