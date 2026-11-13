from datetime import timedelta
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError, PermissionDenied
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from .models import Department, Employee, Task, TaskParticipation, TaskTransfer, WorkNotification
from .transfers import request_transfer, decide, visible, can_decide
from .collaboration import change, OfficialTaskForm


@override_settings(WORKHUB_REQUIRE_HIWORKS=False, WORKHUB_SCHEDULER_ENABLED=False,
    PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class TransferTests(TestCase):
    def setUp(self):
        self.team = Department.objects.create(name='Support')
        self.other = Department.objects.create(name='Sales')
        self.admin = get_user_model().objects.create_superuser('admin')
        self.source = self.employee('source')
        self.target = self.employee('target')
        self.leader = self.employee('leader', position='leader')
        self.rep = self.employee('representative', position='executive')
        self.task = Task.objects.create(title='Transfer work', creator=self.admin, assignee=self.source,
            workflow_version=2, status='doing', progress=60, start_date=timezone.localdate(),
            due_date=timezone.localdate()+timedelta(days=3), accepted_at=timezone.now())
        self.assignment = TaskParticipation.objects.create(task=self.task, employee=self.source,
            accepted_at=timezone.now(), progress=60)

    def employee(self, name, team=None, **kwargs):
        user = get_user_model().objects.create_user(name, email=name+'@example.test')
        return Employee.objects.create(user=user, name=name, department=team or self.team, **kwargs)

    def request(self, user=None, **kwargs):
        self.task.refresh_from_db()
        return request_transfer(user or self.source.user, self.task.pk, self.source, kwargs.pop('target', self.target),
            'Need replacement', 'Remaining work and supporting documents', self.task.revision)

    def approve(self, item, who=None):
        item.refresh_from_db()
        return decide(who or item.steps.filter(decision='pending').first().approver.user,
            item.pk, item.revision, 'approve')

    def test_request_preserves_assignment_then_approval_requires_new_acceptance(self):
        item=self.request()
        self.task.refresh_from_db()
        self.assertEqual(self.task.assignee_id, self.source.pk)
        self.assertEqual(self.task.progress,60)
        self.assertEqual(item.steps.get().approver,self.leader)
        self.approve(item)
        self.task.refresh_from_db();item.refresh_from_db()
        self.assertEqual(self.task.assignee_id,self.target.pk)
        self.assertEqual(self.task.progress,0)
        self.assertEqual(item.state,'acceptance')
        self.assertFalse(self.task.assignments.filter(employee=self.source).exists())
        self.assertEqual(item.source_assignment['progress'],'60')
        with self.assertRaises(ValidationError):
            change(self.target.user,self.task.pk,'progress',{'revision':self.task.revision,'body':'report','progress':20})
        change(self.target.user,self.task.pk,'accept',{'revision':self.task.revision,'body':'accepted'})
        self.task.refresh_from_db();item.refresh_from_db()
        self.assertEqual(item.state,'done');self.assertEqual(self.task.progress,1)
        self.assertIsNotNone(item.accepted_at)

    def test_shared_performer_transfer_preserves_primary_and_other_progress(self):
        helper=self.employee('helper')
        TaskParticipation.objects.create(task=self.task,employee=helper,accepted_at=timezone.now(),progress=80)
        self.source=helper
        item=self.request();self.approve(item)
        self.task.refresh_from_db()
        self.assertEqual(self.task.assignee_id,self.assignment.employee_id)
        self.assertEqual(self.task.assignments.get(employee=self.assignment.employee).progress,60)
        self.assertEqual(self.task.progress,30)

    def test_cross_team_waits_for_receiving_leader(self):
        target=self.employee('receiver',team=self.other)
        receiving=self.employee('receiving-leader',team=self.other,position='leader')
        item=self.request(target=target)
        self.assertEqual(item.steps.count(),2)
        from .permissions import visible_tasks
        self.assertTrue(visible_tasks(receiving.user).filter(pk=self.task.pk).exists())
        with self.assertRaises(PermissionDenied):
            decide(receiving.user,item.pk,0,'approve')
        self.approve(item)
        self.task.refresh_from_db();self.assertEqual(self.task.assignee_id,self.source.pk)
        self.approve(item,receiving.user)
        self.task.refresh_from_db();self.assertEqual(self.task.assignee_id,target.pk)

    def test_leader_task_escalates_to_representative(self):
        self.source.position='leader';self.source.save()
        item=self.request();self.assertEqual(item.steps.get().approver,self.rep)

    def test_leader_request_cannot_approve_own_request(self):
        item=self.request(self.leader.user)
        self.assertEqual(item.steps.get().approver,self.rep)
        with self.assertRaises(PermissionDenied):decide(self.leader.user,item.pk,0,'approve')
        with self.assertRaises(PermissionDenied):decide(self.admin,item.pk,0,'approve')

    def test_inactive_site_leader_is_not_silently_skipped(self):
        self.leader.user.delete();self.leader.refresh_from_db()
        item=self.request()
        self.assertEqual(item.steps.get().approver,self.leader)
        self.assertIsNone(item.steps.get().approver.user_id)
        self.assertFalse(can_decide(self.admin,item))

    def test_reject_and_withdraw_leave_old_assignment(self):
        item=self.request()
        with self.assertRaises(ValidationError):decide(self.leader.user,item.pk,0,'reject')
        decide(self.leader.user,item.pk,0,'reject','Not ready')
        self.task.refresh_from_db();self.assertEqual(self.task.assignee_id,self.source.pk)
        item=self.request();decide(self.source.user,item.pk,0,'withdraw')
        self.task.refresh_from_db();self.assertEqual(self.task.assignee_id,self.source.pk)

    def test_duplicate_stale_and_direct_handover_are_rejected(self):
        item=self.request()
        with self.assertRaises(ValidationError):self.request()
        with self.assertRaises(ValidationError):decide(self.leader.user,item.pk,9,'approve')
        self.approve(item)
        with self.assertRaises(ValidationError):decide(self.leader.user,item.pk,0,'approve')
        self.task.refresh_from_db()
        with self.assertRaises(ValidationError):change(self.admin,self.task.pk,'handover',
            {'revision':self.task.revision,'body':'bypass','replacement':self.source.user})

    def test_revalidate_route_target_and_closed_task(self):
        item=self.request()
        self.target.is_active=False;self.target.save()
        with self.assertRaises(ValidationError):self.approve(item)
        self.target.is_active=True;self.target.save()
        self.source.position='leader';self.source.save()
        with self.assertRaises(ValidationError):self.approve(item)
        self.source.position='member';self.source.save()
        self.task.status='cancelled';self.task.save()
        with self.assertRaises(ValidationError):self.approve(item)

    def test_progress_during_pending_is_preserved_in_snapshot(self):
        item=self.request()
        self.task.refresh_from_db()
        change(self.source.user,self.task.pk,'progress',{'revision':self.task.revision,'body':'new report','progress':85})
        self.approve(item);item.refresh_from_db()
        self.assertEqual(item.source_assignment['progress'],'85')

    def test_existing_participant_and_approver_cannot_be_targets(self):
        TaskParticipation.objects.create(task=self.task,employee=self.target)
        with self.assertRaises(ValidationError):self.request()
        self.task.assignments.filter(employee=self.target).delete()
        from .models import ApprovalStep
        ApprovalStep.objects.create(task=self.task,approver=self.target.user,position=1)
        with self.assertRaises(ValidationError):self.request()

    def test_unrelated_employee_cannot_request_or_read(self):
        stranger=self.employee('stranger',team=self.other)
        with self.assertRaises(Exception) as exc:self.request(stranger.user)
        self.assertIn(type(exc.exception).__name__,['Http404','PermissionDenied'])
        item=self.request()
        self.assertFalse(visible(stranger.user).filter(pk=item.pk).exists())

    def test_no_eligible_approver_blocks_without_reassignment(self):
        self.leader.position='member';self.leader.save()
        self.rep.position='member';self.rep.save()
        with self.assertRaises(ValidationError):self.request()
        self.assertFalse(TaskTransfer.objects.exists())

    def test_alert_and_html_render_without_creating_real_business_data(self):
        item=self.request()
        from .alerts import action_alerts
        self.assertTrue(any(a['key']==f'transfer:{item.pk}' for a in action_alerts(self.leader.user)))
        self.client.force_login(self.admin)
        response=self.client.get(reverse('transfer_detail',args=[item.pk]))
        self.assertEqual(response.status_code,200)
        self.assertContains(response,'이관 승인 순서')
        response=self.client.get(reverse('transfer_new',args=[self.task.pk]))
        self.assertEqual(response.status_code,200)
        self.assertContains(response,'인계 내용')

    def test_edit_cannot_bypass_performer_removal_or_primary_replacement(self):
        data={'title':self.task.title,'assignee':self.target.pk,'status':'doing','importance':'normal','priority':3,
            'start_date':self.task.start_date,'due_date':self.task.due_date,'allocation':'shared','revision':0,
            'management_mode':'report','no_approval':'on'}
        form=OfficialTaskForm(data,instance=self.task,user=self.admin)
        self.assertFalse(form.is_valid())
        self.assertIn('assignee',form.errors)
        self.assertIn('collaborators',form.errors)

    def test_cancelling_parent_closes_open_transfer(self):
        item=self.request();self.task.refresh_from_db()
        change(self.admin,self.task.pk,'cancel',{'revision':self.task.revision,'body':'Cancelled work'})
        item.refresh_from_db();self.assertEqual(item.state,'withdrawn')
        with self.assertRaises(ValidationError):self.approve(item)

    @override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
    def test_transfer_mail_uses_current_state_and_skips_stale_jobs(self):
        from accounts.models import AccessProfile
        from .models import AssignmentMail
        from .assignment_mail import deliver_pending
        from django.core import mail
        for emp in [self.leader,self.source,self.target]:
            AccessProfile.objects.create(user=emp.user,email=emp.user.email,verified_at=timezone.now(),require_mfa=False)
        item=self.request()
        job=AssignmentMail.objects.get(employee=self.leader)
        self.assertEqual(job.payload['kind'],'transfer')
        deliver_pending()
        self.assertTrue(any(message.to==[self.leader.user.email] for message in mail.outbox))
        self.assertIn('이관 내용 확인하기',mail.outbox[-1].alternatives[0].content)
        self.approve(item)
        self.assertEqual(AssignmentMail.objects.filter(employee=self.target).count(),1)

    def test_new_assignee_cannot_retransfer_before_accepting(self):
        item=self.request();self.approve(item);self.task.refresh_from_db()
        another=self.employee('another')
        with self.assertRaises(ValidationError):
            request_transfer(self.target.user,self.task.pk,self.target,another,'Forward',revision=self.task.revision)
