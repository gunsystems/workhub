from datetime import timedelta
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone
from .models import Department, Employee, Task, TaskEvent, ScheduleRegistration, ApprovalStep
from .templatetags.work_identity import work_identity

@override_settings(WORKHUB_REQUIRE_HIWORKS=False, WORKHUB_SCHEDULER_ENABLED=False, PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class DetailIdentityTests(TestCase):
    def test_display_identity_preserves_audit_and_shows_current_schedule(self):
        user = get_user_model().objects.create_superuser('identity@example.test')
        team = Department.objects.create(name='검증부서')
        employee = Employee.objects.create(user=user, name='검증이름', department=team)
        today = timezone.localdate()
        task = Task.objects.create(title='표시 검증', creator=user, assignee=employee, workflow_version=2,
                                   start_date=today, due_date=today+timedelta(days=3))
        event = TaskEvent.objects.create(task=task, author=user, author_name=user.username, kind='created', body='검증 기록')
        registration = ScheduleRegistration.objects.create(task=task, initial_plan={'start_date':'2001-01-01','due_date':'2001-01-02'})
        self.client.force_login(user)
        response = self.client.get(f'/tasks/{task.pk}/')
        self.assertContains(response, '검증부서 · 검증이름')
        self.assertContains(response, '등록일')
        self.assertContains(response, task.due_date.isoformat())
        self.assertNotContains(response, '2001-01-02')
        self.assertContains(response, 'approval-empty')
        event.refresh_from_db(); registration.refresh_from_db()
        self.assertEqual(event.author_name, user.username)
        self.assertEqual(registration.initial_plan['due_date'], '2001-01-02')
        self.assertEqual(work_identity(None, '과거 작성자'), '과거 작성자')
        no_employee = get_user_model().objects.create_user('legacyadmin')
        self.assertEqual(work_identity(no_employee, '기록된 관리자'), '기록된 관리자')
        ApprovalStep.objects.create(task=task, approver=user, approver_name=user.username, position=1, stage=1)
        response = self.client.get(f'/tasks/{task.pk}/')
        self.assertContains(response, 'aria-label="결재 순서"')
        self.assertContains(response, '검증부서 · 검증이름')
        self.assertNotContains(response, 'approval-empty')
