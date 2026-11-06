from datetime import timedelta
from django.test import TestCase
from django.contrib.auth import get_user_model
from django.utils import timezone
from .models import Department, Employee, Task

class WorkhubTests(TestCase):
    def setUp(self):
        self.user=get_user_model().objects.create_superuser('member',password='test-password')
        self.client.force_login(self.user)
        self.dept=Department.objects.create(name='개발팀')
        self.employee=Employee.objects.create(name='개발자',department=self.dept)
        self.today=timezone.localdate()
    def task(self,**kwargs):
        values=dict(title='검증 업무',assignee=self.employee,start_date=self.today-timedelta(days=5),due_date=self.today)
        values.update(kwargs)
        return Task.objects.create(**values)
    def test_login_required(self):
        self.client.logout()
        for path in ['/','/tasks/new/']:
            self.assertEqual(self.client.get(path).status_code,302)
    def test_deadline_boundaries_and_completion(self):
        for offset,expected in [(-1,'overdue'),(0,'soon'),(3,'soon'),(4,'normal')]:
            t=self.task(due_date=self.today+timedelta(days=offset))
            self.assertEqual(t.deadline,expected)
            t.status='done'
            self.assertEqual(t.deadline,'done')
    def test_month_week_day_and_invalid_parameters(self):
        t=self.task()
        for mode,length in [('month',None),('week',7),('day',1)]:
            r=self.client.get('/',{'view':mode,'date':self.today.isoformat()})
            self.assertEqual(r.status_code,200)
            self.assertContains(r,t.title)
            if length: self.assertEqual(len(r.context['days']),length)
        self.assertEqual(self.client.get('/',{'date':'bad','view':'bad','department':'bad'}).status_code,200)
    def test_filters_and_alerts(self):
        self.task(title='초과 업무',due_date=self.today-timedelta(days=1))
        other=Department.objects.get_or_create(name='영업팀')[0]
        employee=Employee.objects.create(name='영업담당',department=other)
        self.task(title='다른 부서 업무',assignee=employee)
        r=self.client.get('/',{'department':self.dept.pk,'employee':self.employee.pk,'status':'todo'})
        self.assertEqual(r.context['count'],1)
        self.assertEqual(len(r.context['overdue']),1)
        self.assertNotContains(r,'다른 부서 업무')
        r=self.client.get('/',{'department':other.pk,'employee':self.employee.pk})
        self.assertEqual(r.context['count'],0)
    def test_create_detail_update_and_validation(self):
        data=dict(title='신규 업무',description='<script>alert(1)</script>',assignee=self.employee.pk,status='doing',importance='high',priority=1,start_date=self.today.isoformat(),due_date=self.today.isoformat())
        r=self.client.post('/tasks/new/',data)
        task=Task.objects.get(title='신규 업무')
        self.assertRedirects(r,f'/tasks/{task.pk}/')
        self.assertContains(self.client.get(r.url),'&lt;script&gt;')
        data['status']='blocked'
        self.client.post(f'/tasks/{task.pk}/edit/',data)
        task.refresh_from_db()
        self.assertEqual(task.status,'blocked')
        data['due_date']=(self.today-timedelta(days=1)).isoformat()
        r=self.client.post(f'/tasks/{task.pk}/edit/',data)
        self.assertEqual(r.status_code,200)
        self.assertContains(r,'마감일은 시작일보다 빠를 수 없습니다.')
        task.refresh_from_db()
        self.assertEqual(task.due_date,self.today)
    def test_multi_day_task_across_month_boundary(self):
        from datetime import date
        task=self.task(start_date=date(2026,10,31),due_date=date(2026,11,2))
        r=self.client.get('/',{'view':'week','date':'2026-11-01'})
        visible=[d for d in r.context['days'] if task in d['tasks']]
        self.assertEqual(len(visible),2)
    def test_regular_employee_login_and_logout(self):
        self.user.is_superuser = False
        self.user.is_staff = False
        self.user.save()
        self.employee.user = self.user
        self.employee.save()
        self.client.logout()
        r=self.client.post('/login/',{'username':'member','password':'test-password'})
        self.assertRedirects(r,'/dashboard/')
        self.assertEqual(self.client.get('/').status_code,200)
        self.client.post('/logout/')
        self.assertRedirects(self.client.get('/'),'/login/?next=/')
