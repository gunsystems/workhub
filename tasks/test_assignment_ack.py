from datetime import timedelta
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase,override_settings
from django.utils import timezone
from .models import Department,Employee,Task,TaskParticipation,TaskEvent
from .collaboration import change

@override_settings(WORKHUB_REQUIRE_HIWORKS=False,WORKHUB_SCHEDULER_ENABLED=False,PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class AssignmentAckTests(TestCase):
    def setUp(self):
        U=get_user_model()
        self.owner=U.objects.create_superuser('requester')
        self.worker=U.objects.create_user('worker')
        team=Department.objects.create(name='담당팀')
        self.emp=Employee.objects.create(user=self.worker,name='담당자',department=team)
        self.task=Task.objects.create(title='명시적 확인 검증',creator=self.owner,assignee=self.emp,workflow_version=2,
            start_date=timezone.localdate(),due_date=timezone.localdate()+timedelta(days=3),assigned_at=timezone.now()-timedelta(hours=27))
        self.part=TaskParticipation.objects.create(task=self.task,employee=self.emp,assigned_at=self.task.assigned_at)
    def act(self,kind,**extra):
        self.task.refresh_from_db()
        return change(self.worker,self.task.pk,kind,{'revision':self.task.revision,'body':'검증 내용',**extra})
    def test_explicit_ack_gate_duplicate_and_progress_floor(self):
        for kind in ['progress','finish','submit']:
            with self.assertRaises(ValidationError): self.act(kind,progress=50)
        self.task.refresh_from_db();self.assertEqual(self.task.revision,0)
        self.assertEqual(TaskEvent.objects.count(),0)
        self.act('comment')
        self.part.refresh_from_db();self.assertIsNone(self.part.accepted_at)
        original_due=self.task.due_date
        self.act('accept')
        self.part.refresh_from_db();self.task.refresh_from_db()
        self.assertEqual(self.part.progress,1);self.assertEqual(self.task.progress,1)
        self.assertEqual(self.task.due_date,original_due)
        self.assertIsNone(self.task.last_report_at)
        stamp=self.part.accepted_at
        with self.assertRaises(ValidationError):self.act('accept')
        self.part.refresh_from_db();self.assertEqual(self.part.accepted_at,stamp)
        self.assertEqual(TaskEvent.objects.filter(kind='accepted').count(),1)
        self.act('progress',progress=40)
        self.task.refresh_from_db();self.assertEqual(self.task.progress,40)
    def test_shared_work_keeps_other_confirmation_pending_and_guides_both_sides(self):
        U=get_user_model();other=U.objects.create_user('coworker')
        helper=Employee.objects.create(user=other,name='공동 담당',department=self.emp.department)
        TaskParticipation.objects.create(task=self.task,employee=helper)
        self.client.force_login(self.worker)
        r=self.client.get(f'/tasks/{self.task.pk}/')
        self.assertContains(r,'먼저 배정된 업무를 확인')
        self.assertContains(r,'1일 3시간 경과')
        self.assertContains(r,'진행 보고 · 확인 후 가능')
        self.client.force_login(self.owner)
        self.assertContains(self.client.get('/work/?status=all'),'배정 확인 대기')
        self.act('accept');self.task.refresh_from_db()
        self.assertTrue(self.task.awaiting_acceptance)
        self.assertEqual(self.task.progress,1)
        self.client.force_login(self.worker)
        r=self.client.get(f'/tasks/{self.task.pk}/')
        self.assertContains(r,'✓ 배정 확인 완료')
        self.assertNotContains(r,'먼저 배정된 업무를 확인')
        self.assertContains(r,'현재 단계: 업무 수행')
