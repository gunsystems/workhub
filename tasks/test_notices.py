from datetime import timedelta
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.core import mail
from django.core.exceptions import ValidationError
from django.test import TestCase,Client,override_settings
from django.utils import timezone
from accounts.models import AccessProfile
from .models import Department,Employee,Notice,NoticeRecipient,NoticeDelivery
from .notices import queue,cancel,publish_due,deliver_pending


@override_settings(WORKHUB_REQUIRE_HIWORKS=False,WORKHUB_SCHEDULER_ENABLED=False,
    PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'],EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
    WORKHUB_BASE_URL='https://workhub.gunsystems.co.kr')
class NoticeTests(TestCase):
    def setUp(self):
        U=get_user_model();self.root=U.objects.create_superuser('notice-operator')
        self.team=Department.objects.create(name='공지팀');self.other=Department.objects.create(name='다른공지팀')
        self.employees=[]
        for index,team in enumerate([self.team,self.team,self.other]):
            user=U.objects.create_user(f'notice-worker-{index}',email=f'notice{index}@gunsystems.co.kr')
            AccessProfile.objects.create(user=user,email=user.email,verified_at=timezone.now(),require_mfa=False)
            self.employees.append(Employee.objects.create(user=user,name=f'공지직원{index}',department=team))
        self.client.force_login(self.root);mail.outbox=[]

    def notice(self,**kwargs):
        return Notice.objects.create(title='회사 공지',body='회의 자료를 준비해 주세요.',creator=self.root,
            creator_name='관리자',audience='all',**kwargs)

    def data(self,**kwargs):
        return {'title':'공유 공지','body':'직원 전달 내용','audience':'all','send_email':'on','timing':'now',**kwargs}

    def test_draft_is_previewed_without_any_delivery(self):
        response=self.client.post('/work/notices/new/',self.data())
        self.assertEqual(response.status_code,302)
        notice=Notice.objects.get();self.assertEqual(notice.state,'draft')
        self.assertEqual(len(mail.outbox),0);self.assertFalse(NoticeDelivery.objects.exists())
        preview=self.client.get(response['Location'])
        self.assertContains(preview,'공지직원0');self.assertContains(preview,'확인하고 발송')

    def test_all_team_and_person_audiences(self):
        all_notice=self.notice();queue(all_notice.pk,self.root)
        self.assertEqual(all_notice.recipients.count(),3)
        team=self.notice();team.audience='team';team.save();team.departments.add(self.team);queue(team.pk,self.root)
        self.assertEqual(team.recipients.count(),2)
        person=self.notice();person.audience='person';person.save();person.employees.add(self.employees[2]);queue(person.pk,self.root)
        self.assertEqual(person.recipients.get().employee_id,self.employees[2].pk)

    def test_email_and_messenger_deliver_once_per_selected_channel(self):
        from .models import HiworksLink
        employee=self.employees[0]
        HiworksLink.objects.create(employee=employee,email=employee.user.email,user_id='notice0',remote_id='notice-0')
        notice=self.notice(send_messenger=True);notice.audience='person';notice.save();notice.employees.add(employee)
        queue(notice.pk,self.root);publish_due()
        with patch('tasks.hiworks.api',return_value={}) as notify:
            deliver_pending();deliver_pending();self.assertEqual(notify.call_count,1)
            args=notify.call_args.args;self.assertEqual(args[0],'/office/v2/notify')
            self.assertEqual(args[2]['user_list'],['notice0'])
        self.assertEqual(len(mail.outbox),1)
        self.assertIn('공지사항 확인하기',mail.outbox[0].alternatives[0].content)
        self.assertEqual(set(NoticeDelivery.objects.values_list('state',flat=True)),{'sent'})

    def test_scheduled_notice_is_not_visible_or_sent_early(self):
        future=timezone.now()+timedelta(hours=2)
        notice=self.notice(timing='scheduled',scheduled_for=future)
        queue(notice.pk,self.root);publish_due();deliver_pending()
        self.assertEqual(len(mail.outbox),0)
        self.client.force_login(self.employees[0].user)
        self.assertEqual(self.client.get(f'/work/notices/{notice.pk}/').status_code,404)
        with patch('django.utils.timezone.now',return_value=future+timedelta(seconds=1)):
            publish_due();deliver_pending()
            self.client.force_login(self.employees[0].user)
            self.assertEqual(self.client.get(f'/work/notices/{notice.pk}/').status_code,200)
        self.assertEqual(len(mail.outbox),3)

    def test_cancel_prevents_scheduled_publication_and_delivery(self):
        notice=self.notice();queue(notice.pk,self.root);cancel(notice.pk,self.root)
        publish_due();deliver_pending();notice.refresh_from_db()
        self.assertEqual(notice.state,'cancelled');self.assertEqual(len(mail.outbox),0)
        self.assertFalse(NoticeDelivery.objects.exclude(state='cancelled').exists())

    def test_duplicate_confirmation_does_not_duplicate_recipients(self):
        notice=self.notice();queue(notice.pk,self.root)
        with self.assertRaises(ValidationError):queue(notice.pk,self.root)
        self.assertEqual(notice.recipients.count(),3);self.assertEqual(NoticeDelivery.objects.count(),3)

    def test_non_recipient_cannot_open_notice_and_employee_cannot_write(self):
        notice=self.notice();notice.audience='person';notice.save();notice.employees.add(self.employees[0])
        queue(notice.pk,self.root);publish_due()
        self.client.force_login(self.employees[2].user)
        self.assertEqual(self.client.get(f'/work/notices/{notice.pk}/').status_code,404)
        self.assertNotContains(self.client.get('/work/notices/'),'회사 공지')
        self.assertEqual(self.client.get('/work/notices/new/').status_code,403)
        self.assertEqual(self.client.post(f'/work/notices/{notice.pk}/',{'action':'cancel'}).status_code,404)

    def test_employee_read_records_only_their_own_view(self):
        notice=self.notice();queue(notice.pk,self.root);publish_due()
        self.client.get(f'/work/notices/{notice.pk}/')
        self.assertFalse(notice.recipients.exclude(read_at=None).exists())
        self.client.force_login(self.employees[0].user)
        self.assertEqual(self.client.get(f'/work/notices/{notice.pk}/').status_code,200)
        self.assertIsNotNone(notice.recipients.get(user=self.employees[0].user).read_at)
        self.assertIsNone(notice.recipients.get(user=self.employees[1].user).read_at)

    def test_departed_employee_skipped_and_remaining_recipients_continue(self):
        notice=self.notice();queue(notice.pk,self.root);publish_due()
        employee=self.employees[0];employee.is_active=False;employee.save();deliver_pending()
        self.assertEqual(len(mail.outbox),2)
        self.assertEqual(NoticeDelivery.objects.get(recipient__employee=employee).state,'skipped')

    def test_email_changed_after_confirmation_not_sent_to_new_address(self):
        notice=self.notice();queue(notice.pk,self.root);publish_due()
        AccessProfile.objects.filter(user=self.employees[0].user).update(email='different@gunsystems.co.kr')
        deliver_pending();self.assertEqual(len(mail.outbox),2)

    def test_frozen_targets_do_not_add_later_hires(self):
        notice=self.notice();queue(notice.pk,self.root)
        new=get_user_model().objects.create_user('later-hire',email='later@gunsystems.co.kr')
        AccessProfile.objects.create(user=new,email=new.email,verified_at=timezone.now(),require_mfa=False)
        Employee.objects.create(user=new,name='후입사',department=self.team)
        publish_due();deliver_pending();self.assertEqual(len(mail.outbox),3)

    def test_delivery_failure_is_not_blindly_retried_and_not_leaked(self):
        notice=self.notice();queue(notice.pk,self.root);publish_due()
        with patch('accounts.services.deliver',side_effect=RuntimeError('private-token')) as send:
            deliver_pending();deliver_pending();self.assertEqual(send.call_count,3)
        self.assertFalse(NoticeDelivery.objects.exclude(state='unknown').exists())
        self.assertNotContains(self.client.get(f'/work/notices/{notice.pk}/'),'private-token')

    def test_messenger_failure_does_not_block_email(self):
        notice=self.notice(send_messenger=True);queue(notice.pk,self.root);publish_due()
        with patch('tasks.hiworks.api',side_effect=RuntimeError()):deliver_pending(limit=10)
        self.assertEqual(len(mail.outbox),3)
        self.assertEqual(NoticeDelivery.objects.filter(channel='email',state='sent').count(),3)

    def test_stale_membership_defers_delivery_without_bypass(self):
        notice=self.notice();queue(notice.pk,self.root);publish_due()
        with override_settings(WORKHUB_REQUIRE_HIWORKS=True),patch('accounts.hiworks_access.fresh_cache',return_value=None):deliver_pending()
        self.assertFalse(NoticeDelivery.objects.exclude(state='pending').exists())
        self.assertEqual(len(mail.outbox),0)

    def test_cancel_after_publication_is_rejected(self):
        notice=self.notice();queue(notice.pk,self.root);publish_due()
        with self.assertRaises(ValidationError):cancel(notice.pk,self.root)

    def test_invalid_channel_audience_and_past_schedule_are_rejected(self):
        from .notice_views import NoticeForm
        self.assertFalse(NoticeForm(self.data(send_email='',send_messenger='')).is_valid())
        self.assertFalse(NoticeForm(self.data(audience='team')).is_valid())
        self.assertFalse(NoticeForm(self.data(audience='person')).is_valid())
        self.assertFalse(NoticeForm(self.data(timing='scheduled',scheduled_for=(timezone.now()-timedelta(hours=1)).isoformat())).is_valid())

    def test_no_verified_target_blocks_confirmation(self):
        AccessProfile.objects.update(verified_at=None)
        with self.assertRaises(ValidationError):queue(self.notice().pk,self.root)
        self.assertFalse(NoticeRecipient.objects.exists())

    def test_confirm_and_cancel_require_csrf(self):
        notice=self.notice();client=Client(enforce_csrf_checks=True);client.force_login(self.root)
        self.assertEqual(client.post(f'/work/notices/{notice.pk}/',{'action':'send'}).status_code,403)
        self.assertEqual(Notice.objects.get(pk=notice.pk).state,'draft')

    def test_notice_content_is_escaped_in_app_and_html_email(self):
        notice=self.notice();notice.body='<script>alert(1)</script>';notice.save()
        queue(notice.pk,self.root);publish_due();deliver_pending()
        self.assertNotContains(self.client.get(f'/work/notices/{notice.pk}/'),'<script>alert(1)</script>')
        self.assertIn('&lt;script&gt;',mail.outbox[0].alternatives[0].content)

    def test_audit_event_results_are_named_without_empty_json(self):
        from .audit import write
        write('mail_sent','AssignmentMail',1,'업무 배정 메일',after={'employee_id':self.employees[0].pk,'result':'메일 서버 접수'})
        response=self.client.get('/work/system-log/',{'action':'mail_sent'})
        self.assertContains(response,'공지직원0');self.assertContains(response,'처리 결과')
        self.assertNotContains(response,'<pre>{}</pre>')
        self.assertNotContains(response,'employee_id')
