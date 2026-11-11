import json
from time import time
from unittest.mock import patch
from datetime import timedelta
from django.contrib.auth import get_user_model
from django.core import mail
from django.db import transaction
from django.test import TestCase, Client, override_settings
from django.utils import timezone
from accounts.models import AccessProfile
from accounts.idle import KEY, TIMEOUT
from .models import (Department,Employee,Task,TaskParticipation,AssignmentMail,SystemAudit,PersonalBoard,PersonalColumn,PersonalCard)
from .assignment_mail import enqueue, deliver_pending


@override_settings(WORKHUB_REQUIRE_HIWORKS=False,WORKHUB_SCHEDULER_ENABLED=False,
    PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'],EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
    WORKHUB_BASE_URL='https://workhub.gunsystems.co.kr')
class SecurityNotificationTests(TestCase):
    @override_settings(HIWORKS_NOTIFY_TOKEN='test-only')
    def test_comment_alerts_assignee_not_actor_and_queues_email_once(self):
        from .collaboration import change
        from .models import WorkNotification,HiworksLink,HiworksDelivery
        HiworksLink.objects.create(employee=self.emp,user_id='worker',remote_id='worker',email=self.worker.email,notifications_enabled=True)
        change(self.root,self.task.pk,'comment',{'revision':0,'body':'새 댓글입니다.'})
        notices=WorkNotification.objects.filter(task=self.task)
        self.assertEqual(list(notices.values_list('user_id',flat=True)),[self.worker.pk])
        self.assertEqual(HiworksDelivery.objects.count(),1)
        jobs=AssignmentMail.objects.filter(payload__kind='comment');self.assertEqual(jobs.count(),1)
        deliver_pending();deliver_pending();self.assertEqual(len(mail.outbox),1)
        self.assertIn('업무 의견 확인하기',mail.outbox[0].alternatives[0].content)
    def test_comment_notification_channel_opt_out_still_keeps_inapp(self):
        from .collaboration import change
        from .models import WorkNotification,HiworksDelivery
        self.task.assignment_email=False;self.task.assignment_messenger=False;self.task.save()
        change(self.root,self.task.pk,'comment',{'revision':0,'body':'내부 알림만'})
        self.assertTrue(WorkNotification.objects.filter(user=self.worker).exists());self.assertFalse(AssignmentMail.objects.exists());self.assertFalse(HiworksDelivery.objects.exists())
    def test_comment_email_rechecks_current_access(self):
        from .collaboration import change
        change(self.root,self.task.pk,'comment',{'revision':0,'body':'의견'})
        self.worker.is_active=False;self.worker.save();deliver_pending()
        self.assertEqual(len(mail.outbox),0);self.assertEqual(AssignmentMail.objects.get().state,'skipped')
    def setUp(self):
        U=get_user_model()
        self.root=U.objects.create_superuser('operator',password='root-test-secret')
        self.worker=U.objects.create_user('worker',email='worker@gunsystems.co.kr',password='employee-test-secret')
        self.helper=U.objects.create_user('helper',email='helper@gunsystems.co.kr')
        self.team=Department.objects.create(name='테스트팀')
        self.emp=Employee.objects.create(user=self.worker,name='담당자',department=self.team)
        self.assist=Employee.objects.create(user=self.helper,name='협업자',department=self.team)
        # MFA policy is exercised elsewhere; these fixtures isolate mail and idle behavior.
        for u in [self.worker,self.helper]: AccessProfile.objects.create(user=u,email=u.email,verified_at=timezone.now(),require_mfa=False)
        self.day=timezone.localdate()
        self.task=Task.objects.create(title='배정 검증',creator=self.root,assignee=self.emp,workflow_version=2,start_date=self.day,due_date=self.day)
        TaskParticipation.objects.create(task=self.task,employee=self.emp)
        self.client.force_login(self.root)
        mail.outbox=[]

    def data(self,**kwargs):
        return {'title':'신규 업무','assignee':self.emp.pk,'start_date':self.day,'due_date':self.day,'status':'todo',
            'importance':'normal','priority':3,'allocation':'shared','revision':0,'management_mode':'event',**kwargs}

    def test_new_assignment_queues_without_sending_in_request(self):
        r=self.client.post('/work/new/',self.data())
        self.assertEqual(r.status_code,302)
        self.assertEqual(AssignmentMail.objects.count(),1);self.assertEqual(len(mail.outbox),0)
        deliver_pending()
        self.assertEqual(len(mail.outbox),1)
        message=mail.outbox[0];self.assertEqual(message.to,[self.worker.email])
        self.assertIn('배정된 업무 확인하기',message.alternatives[0].content)
        self.assertIn('https://workhub.gunsystems.co.kr/tasks/',message.alternatives[0].content)
        self.assertEqual(AssignmentMail.objects.get().state,'sent')

    def test_identical_enqueue_and_worker_retry_do_not_duplicate(self):
        enqueue(self.task,self.root);enqueue(self.task,self.root)
        deliver_pending();deliver_pending()
        self.assertEqual(AssignmentMail.objects.count(),1);self.assertEqual(len(mail.outbox),1)

    def test_draft_no_email_then_publish(self):
        self.client.post('/work/new/',self.data(status='draft'))
        obj=Task.objects.latest('pk');self.assertFalse(AssignmentMail.objects.exists())
        self.client.post(f'/tasks/{obj.pk}/edit/',self.data(revision=obj.revision))
        self.assertEqual(AssignmentMail.objects.count(),1)

    def test_ordinary_edit_does_not_email_again(self):
        self.client.post('/work/new/',self.data());obj=Task.objects.latest('pk')
        r=self.client.post(f'/tasks/{obj.pk}/edit/',self.data(title='수정한 내용',revision=obj.revision,change_reason='내용 정리'))
        self.assertEqual(r.status_code,302);self.assertEqual(AssignmentMail.objects.count(),1)

    def test_new_collaborator_gets_one_additional_email(self):
        self.client.post('/work/new/',self.data());obj=Task.objects.latest('pk')
        self.client.post(f'/tasks/{obj.pk}/edit/',self.data(collaborators=[self.assist.pk],revision=obj.revision,change_reason='협업자 추가'))
        self.assertEqual(set(AssignmentMail.objects.values_list('employee_id',flat=True)),{self.emp.pk,self.assist.pk})

    def test_individual_allocation_no_shared_notification_leak(self):
        self.client.post('/work/new/',self.data(collaborators=[self.assist.pk],allocation='individual'))
        self.assertEqual(AssignmentMail.objects.count(),2)
        for job in AssignmentMail.objects.all():
            self.assertEqual(job.task.assignee_id,job.employee_id)

    def test_handover_queues_for_new_assignee_only(self):
        from .collaboration import change
        change(self.root,self.task.pk,'handover',{'revision':0,'replacement':self.helper,'body':'인계'})
        self.assertEqual(AssignmentMail.objects.get().employee_id,self.assist.pk)

    def test_queue_rolls_back_with_task_transaction(self):
        try:
            with transaction.atomic():
                enqueue(self.task,self.root)
                raise ValueError('rollback')
        except ValueError: pass
        self.assertFalse(AssignmentMail.objects.exists())

    def test_removed_collaborator_not_mailed(self):
        TaskParticipation.objects.create(task=self.task,employee=self.assist)
        enqueue(self.task,self.root,{self.assist.pk})
        self.task.assignments.filter(employee=self.assist).delete();deliver_pending()
        self.assertEqual(AssignmentMail.objects.get().state,'skipped');self.assertEqual(len(mail.outbox),0)

    def test_inactive_account_not_mailed(self):
        enqueue(self.task,self.root);self.worker.is_active=False;self.worker.save();deliver_pending()
        self.assertEqual(AssignmentMail.objects.get().state,'skipped')

    def test_unverified_account_never_queued(self):
        AccessProfile.objects.filter(user=self.worker).update(verified_at=None)
        enqueue(self.task,self.root);self.assertFalse(AssignmentMail.objects.exists())

    def test_cancelled_task_not_mailed(self):
        enqueue(self.task,self.root);self.task.status='cancelled';self.task.save();deliver_pending()
        self.assertEqual(AssignmentMail.objects.get().state,'skipped')

    def test_unknown_delivery_never_automatically_retried_and_error_scrubbed(self):
        enqueue(self.task,self.root)
        with patch('accounts.services.deliver',side_effect=RuntimeError('sensitive-token')) as send:
            deliver_pending();deliver_pending();self.assertEqual(send.call_count,1)
        job=AssignmentMail.objects.get();self.assertEqual(job.state,'unknown')
        self.assertNotIn('sensitive-token',job.result)
        self.assertNotIn('sensitive-token',json.dumps(list(SystemAudit.objects.values()),default=str))

    def test_interrupted_send_not_automatically_duplicated(self):
        enqueue(self.task,self.root)
        AssignmentMail.objects.update(state='sending',claimed_until=timezone.now()-timedelta(seconds=1))
        deliver_pending();self.assertEqual(AssignmentMail.objects.get().state,'unknown');self.assertEqual(len(mail.outbox),0)

    def set_idle(self,age):
        session=self.client.session;session[KEY]=time()-age;session.save()

    def test_timeout_blocks_business_post_at_boundary(self):
        self.set_idle(TIMEOUT)
        r=self.client.post('/work/new/',self.data())
        self.assertRedirects(r,'/login/?expired=1',fetch_redirect_response=False)
        self.assertEqual(Task.objects.count(),1)
        self.assertNotIn('_auth_user_id',self.client.session)
        self.assertTrue(SystemAudit.objects.filter(action='session_timeout',actor_id_snapshot=self.root.pk).exists())

    def test_root_also_expires_and_normal_navigation_renews(self):
        self.set_idle(1500)
        self.assertEqual(self.client.get('/dashboard/',HTTP_SEC_FETCH_MODE='navigate').status_code,200)
        self.assertLess(time()-self.client.session[KEY],3)

    def test_status_reads_and_background_gets_never_renew(self):
        self.set_idle(1000);last=self.client.session[KEY]
        self.client.get('/accounts/session/status/');self.client.get('/dashboard/')
        self.assertEqual(last,self.client.session[KEY])

    def test_activity_post_renews_and_activity_get_disallowed(self):
        self.set_idle(1000)
        self.assertEqual(self.client.post('/accounts/session/activity/').status_code,200)
        self.assertLess(time()-self.client.session[KEY],3)
        self.assertEqual(self.client.get('/accounts/session/activity/').status_code,405)

    def test_expired_activity_cannot_revive_session(self):
        self.set_idle(TIMEOUT+1)
        self.assertEqual(self.client.post('/accounts/session/activity/').status_code,401)
        self.assertNotIn('_auth_user_id',self.client.session)

    def test_expired_cookie_and_anonymous_status_return_401(self):
        self.client.logout()
        self.assertEqual(self.client.get('/accounts/session/status/').status_code,401)

    def test_activity_requires_csrf(self):
        c=Client(enforce_csrf_checks=True);c.force_login(self.root)
        self.assertEqual(c.post('/accounts/session/activity/').status_code,403)

    def test_before_after_and_actor_recorded_from_page(self):
        self.client.post('/work/new/',self.data())
        obj=Task.objects.latest('pk')
        self.client.post(f'/tasks/{obj.pk}/edit/',self.data(title='변경 후 업무명',revision=obj.revision,change_reason='명칭 정리'))
        events=SystemAudit.objects.filter(target_type='Task',target_id=str(obj.pk),action='update')
        self.assertTrue(any(e.before.get('업무명')=='신규 업무' and e.after.get('업무명')=='변경 후 업무명' and e.actor_id_snapshot==self.root.pk for e in events))

    def test_delete_history_survives_object_deletion(self):
        pk=self.task.pk;self.task.delete()
        event=SystemAudit.objects.get(target_type='Task',target_id=str(pk),action='delete')
        self.assertEqual(event.before['업무명'],'배정 검증')

    def test_audit_rolled_back_with_mutation(self):
        count=SystemAudit.objects.count()
        try:
            with transaction.atomic():
                self.task.title='롤백';self.task.save();raise ValueError()
        except ValueError: pass
        self.assertEqual(SystemAudit.objects.count(),count)

    def test_private_contents_and_secrets_not_audited(self):
        board=PersonalBoard.objects.create(owner=self.worker,name='private-board-secret')
        column=PersonalColumn.objects.create(board=board,name='private-column-secret')
        PersonalCard.objects.create(column=column,title='private-card-secret',body='private-body-secret')
        serialized=json.dumps(list(SystemAudit.objects.values()),default=str)
        for forbidden in ['private-board-secret','private-column-secret','private-card-secret','private-body-secret','root-test-secret','employee-test-secret',self.worker.password]:
            self.assertNotIn(forbidden,serialized)

    def test_draft_task_contents_not_in_admin_log(self):
        self.task.status='draft';self.task.title='draft-secret-title';self.task.description='draft-secret-body';self.task.save()
        event=SystemAudit.objects.filter(target_type='Task',target_id=str(self.task.pk),action='update').latest('pk')
        self.assertNotIn('draft-secret',json.dumps(event.after));self.assertEqual(event.target_label,'비공개 개인 항목')

    def test_only_root_admin_can_read_audit(self):
        self.assertEqual(self.client.get('/work/system-log/').status_code,200)
        for user in [self.worker,self.helper]:
            self.client.force_login(user);self.assertEqual(self.client.get('/work/system-log/').status_code,403)
        self.helper.is_staff=True;self.helper.save()
        self.client.force_login(self.helper);self.assertEqual(self.client.get('/work/system-log/').status_code,403)

    def test_audit_ui_filters_and_escapes_content(self):
        self.task.title='<script>bad()</script>';self.task.save()
        response=self.client.get('/work/system-log/',{'q':'<script>','action':'update'})
        self.assertContains(response,'&lt;script&gt;');self.assertNotContains(response,'<script>bad()')
        self.assertContains(response,'시스템 로그');self.assertContains(response,'변경 전·후 보기')

    def test_session_script_and_timeout_notice_present(self):
        self.assertContains(self.client.get('/dashboard/'),'tasks/idle.')
        self.client.logout()
        self.assertContains(self.client.get('/login/?expired=1'),'30분 동안 활동이 없어')
