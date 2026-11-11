from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase,override_settings
from django.utils import timezone
from .models import Task,Employee,Department,TaskParticipation,TaskEvent
from .collaboration import change
from .progress_reporting import next_report_number

@override_settings(WORKHUB_REQUIRE_HIWORKS=False,WORKHUB_SCHEDULER_ENABLED=False,PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class ProgressNumberTests(TestCase):
    def test_number_and_previous_progress_are_per_performer(self):
        U=get_user_model();user=U.objects.create_user('reporter');other=U.objects.create_user('helper')
        team=Department.objects.create(name='보고팀')
        emp=Employee.objects.create(user=user,name='보고자',department=team)
        helper=Employee.objects.create(user=other,name='공동자',department=team)
        task=Task.objects.create(title='보고 차수 확인',assignee=emp,creator=user,workflow_version=2,start_date=timezone.localdate(),due_date=timezone.localdate())
        TaskParticipation.objects.create(task=task,employee=emp,accepted_at=timezone.now(),progress=1)
        TaskParticipation.objects.create(task=task,employee=helper,accepted_at=timezone.now(),progress=80)
        TaskEvent.objects.create(task=task,author=other,author_name='공동자',kind='progress',body='별도 보고',snapshot={'action':'progress'})
        self.client.force_login(user)
        url=f'/work/{task.pk}/progress/'
        # Discover the named route rather than relying on its path spelling.
        from django.urls import reverse
        url=reverse('official_action',kwargs={'pk':task.pk,'kind':'progress'})
        r=self.client.get(url)
        self.assertContains(r,'진행 보고 1차')
        self.assertEqual(r.context['form']['progress'].value(),1)
        change(user,task.pk,'progress',{'revision':task.revision,'body':'1차 보고','progress':20})
        task.refresh_from_db()
        r=self.client.get(url)
        self.assertContains(r,'진행 보고 2차')
        self.assertEqual(r.context['form']['progress'].value(),20)
        self.assertEqual(next_report_number(task,other),2)
        failed=self.client.post(url,{'revision':task.revision,'body':'잘못된 입력','progress':101})
        self.assertEqual(failed.status_code,200)
        self.assertEqual(str(failed.context['form']['progress'].value()),'101')
        self.assertEqual(next_report_number(task,user),2)
        change(user,task.pk,'progress',{'revision':task.revision,'body':'2차 보고','progress':40})
        task.refresh_from_db()
        report=task.events.filter(author=user,kind='progress').first()
        self.assertEqual(report.snapshot['progress_report_number'],2)
        self.assertEqual(report.snapshot['performer_progress'],40)
        self.assertContains(self.client.get(f'/tasks/{task.pk}/'),'진행 보고 3차')
        TaskEvent.objects.create(task=task,author=user,author_name='보고자',kind='progress',body='수행 완료',snapshot={'action':'finish'})
        self.assertEqual(next_report_number(task,user),3)
        cached=Task.objects.prefetch_related('events').get(pk=task.pk)
        self.assertEqual(next_report_number(cached,user),3)
    def test_legacy_progress_form_starts_at_current_value(self):
        U=get_user_model();u=U.objects.create_user('legacyreport')
        team=Department.objects.create(name='기존팀');e=Employee.objects.create(user=u,name='기존담당',department=team)
        t=Task.objects.create(title='기존 업무',assignee=e,start_date=timezone.localdate(),due_date=timezone.localdate(),accepted_at=timezone.now(),progress=20)
        TaskEvent.objects.create(task=t,author=u,author_name='기존담당',kind='progress',body='첫 보고',snapshot={})
        self.client.force_login(u)
        r=self.client.get(f'/tasks/{t.pk}/actions/progress/')
        self.assertContains(r,'진행 보고 2차')
        self.assertEqual(r.context['form']['progress'].value(),20)
