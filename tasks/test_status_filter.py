from django.contrib.auth import get_user_model
from django.test import TestCase,override_settings
from django.utils import timezone
from .models import Department,Employee,Task


@override_settings(WORKHUB_REQUIRE_HIWORKS=False,WORKHUB_SCHEDULER_ENABLED=False,PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class StatusFilterTests(TestCase):
    def test_all_includes_closed_work_without_revealing_private_or_foreign_work(self):
        U=get_user_model();user=U.objects.create_user('viewer');other=U.objects.create_user('foreign')
        team=Department.objects.create(name='조회팀');foreign=Department.objects.create(name='외부조회팀')
        emp=Employee.objects.create(name='조회직원',user=user,department=team)
        outsider=Employee.objects.create(name='다른직원',user=other,department=foreign)
        today=timezone.localdate()
        for state in ['doing','done','cancelled']:
            Task.objects.create(title='조회대상-'+state,creator=user,assignee=emp,start_date=today,due_date=today,status=state)
        Task.objects.create(title='다른사람 비공개',creator=other,assignee=emp,start_date=today,due_date=today,status='draft')
        Task.objects.create(title='다른팀 완료업무',creator=other,assignee=outsider,start_date=today,due_date=today,status='done')
        self.client.force_login(user)
        default=self.client.get('/work/');self.assertContains(default,'조회대상-doing');self.assertNotContains(default,'조회대상-done')
        all_tasks=self.client.get('/work/?status=all');self.assertContains(all_tasks,'조회대상-doing');self.assertContains(all_tasks,'조회대상-done');self.assertContains(all_tasks,'조회대상-cancelled')
        self.assertNotContains(all_tasks,'다른사람 비공개');self.assertNotContains(all_tasks,'다른팀 완료업무')
        self.assertContains(all_tasks,'value="all" selected');self.assertContains(self.client.get('/work/?status=all&q=cancelled'),'조회대상-cancelled')
        self.assertNotContains(self.client.get('/work/?status=all&q=cancelled'),'조회대상-done')
        self.assertNotContains(self.client.get('/work/?status=unknown'),'조회대상-done')
