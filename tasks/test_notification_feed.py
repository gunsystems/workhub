from datetime import timedelta
from types import SimpleNamespace as Row
from django.contrib.auth import get_user_model
from django.test import TestCase, SimpleTestCase, override_settings
from django.utils import timezone
from accounts.models import AccessProfile
from .models import Department, Employee, Task, WorkNotification, Proposal, ProposalEvent, ProposalNotice
from .notification_feed import build_feed


class FeedOrderingTests(SimpleTestCase):
    def test_mixed_types_use_notification_time_not_type_or_future_schedule(self):
        now = timezone.now()
        work = Row(pk=1, created_at=now, read_at=None, message='배정 안내', task=Row(title='최신 업무'))
        proposal = Row(pk=1, kind='result', created_at=now-timedelta(hours=1), read_at=now,
            message='승인 결과', event=Row(proposal=Row(pk=9,state='approved',title='장비 구매',get_state_display=lambda:'승인 완료')))
        personal = Row(pk=1, delivered_at=now-timedelta(hours=2), scheduled_for=now+timedelta(days=3),
            read_at=None, card=Row(title='다음 주 일정'))
        rows = build_feed(work=[work],proposals=[proposal],personal=[personal],requests=[],quick=[],followups=[],leave=[],certificates=[])
        self.assertEqual([r['title'] for r in rows], ['최신 업무','장비 구매','다음 주 일정'])
        self.assertTrue(rows[0]['post'])
        self.assertFalse(rows[1]['post'])
        self.assertFalse(rows[1]['unread'])
        self.assertEqual(rows[1]['url'], '/work/approvals/proposals/9/')

    def test_closed_followup_keeps_title_and_history_without_action(self):
        row = Row(pk=4,created_at=timezone.now(),read_at=None,closed=True,current_state='취소 확인 완료',
            item_id=7,item=Row(title='계정 정리',case=Row(employee=Row(name='직원'),state='cancelled')))
        result = build_feed(work=[],proposals=[],personal=[],requests=[],quick=[],followups=[row],leave=[],certificates=[])[0]
        self.assertEqual(result['title'],'직원 · 계정 정리')
        self.assertEqual(result['url'],'')
        self.assertFalse(result['unread'])

    def test_pending_proposal_uses_current_terminal_state(self):
        for state, label in [('approved','승인 완료'),('rejected','반려'),('withdrawn','회수')]:
            with self.subTest(state=state):
                row=Row(pk=1,kind='pending',created_at=timezone.now(),read_at=None,
                    actionable=False,message='품의 결재가 필요합니다.',event=Row(proposal=Row(
                        pk=9,state=state,title='구매',get_state_display=lambda:label)))
                result=build_feed(work=[],personal=[],requests=[],quick=[],followups=[],leave=[],proposals=[row],certificates=[])[0]
                self.assertTrue(result['closed'])
                self.assertFalse(result['unread'])
                self.assertEqual(result['status_label'],label)
                self.assertNotIn('결재가 필요',result['message'])
                self.assertEqual(result['button'],'처리 결과 보기')
                self.assertEqual(result['url'],'/work/approvals/proposals/9/')

    def test_current_pending_is_actionable_but_previous_step_is_closed(self):
        row=Row(pk=1,kind='pending',created_at=timezone.now(),read_at=None,
            actionable=True,message='품의 결재가 필요합니다.',event=Row(proposal=Row(
                pk=9,state='pending',title='구매',get_state_display=lambda:'결재 중')))
        def result():
            return build_feed(work=[],personal=[],requests=[],quick=[],followups=[],leave=[],proposals=[row],certificates=[])[0]
        self.assertFalse(result()['closed'])
        self.assertTrue(result()['unread'])
        self.assertEqual(result()['message'],row.message)
        row.actionable=False
        self.assertTrue(result()['closed'])
        self.assertEqual(result()['status_label'],'내 처리 종료')
        self.assertNotIn('결재가 필요',result()['message'])


@override_settings(WORKHUB_REQUIRE_HIWORKS=False,WORKHUB_SCHEDULER_ENABLED=False)
class NotificationFeedTests(TestCase):
    def setUp(self):
        User=get_user_model()
        self.user=User.objects.create_user('feed-user',email='feed@example.test')
        self.other=User.objects.create_user('feed-other',email='other@example.test')
        dept=Department.objects.create(name='업무팀');private=Department.objects.create(name='비공개팀')
        self.employee=Employee.objects.create(user=self.user,name='수신자',department=dept)
        self.private_employee=Employee.objects.create(user=self.other,name='다른 직원',department=private)
        AccessProfile.objects.create(user=self.user,email=self.user.email,verified_at=timezone.now(),require_mfa=False)
        self.client.force_login(self.user)

    def task(self,employee,title):
        return Task.objects.create(creator=employee.user,assignee=employee,title=title,
            start_date=timezone.localdate(),due_date=timezone.localdate())

    def proposal_notice(self,employee,title,at):
        proposal=Proposal.objects.create(employee=employee,title=title,category='other',body='검증',state='approved')
        event=ProposalEvent.objects.create(proposal=proposal,actor=employee.user,actor_name=employee.name,action='승인')
        return ProposalNotice.objects.create(event=event,user=self.user,kind='result',email=self.user.email,message='결과 확인',created_at=at)

    def test_global_latest_order_real_titles_and_private_items_filtered(self):
        now=timezone.now()
        task=self.task(self.employee,'가장 최신 업무')
        WorkNotification.objects.create(user=self.user,task=task,message='새 배정',created_at=now)
        self.proposal_notice(self.employee,'품의 제목 <script>alert(1)</script>',now-timedelta(hours=1))
        WorkNotification.objects.create(user=self.other,task=task,message='다른 수신자 알림',created_at=now+timedelta(hours=1))
        private_task=self.task(self.private_employee,'비공개 업무 제목')
        WorkNotification.objects.create(user=self.user,task=private_task,message='과거 접근',created_at=now+timedelta(hours=2))
        self.proposal_notice(self.private_employee,'비공개 품의 제목',now+timedelta(hours=3))
        response=self.client.get('/work/notifications/')
        self.assertEqual(response.status_code,200)
        self.assertEqual([r['title'] for r in response.context['notification_feed']],
            ['가장 최신 업무','품의 제목 <script>alert(1)</script>'])
        self.assertNotContains(response,'비공개 업무 제목')
        self.assertNotContains(response,'비공개 품의 제목')
        self.assertNotContains(response,'다른 수신자 알림')
        self.assertContains(response,'&lt;script&gt;alert(1)&lt;/script&gt;')
        self.assertNotContains(response,'<script>alert(1)</script>')

    def test_empty_feed_and_read_post_still_work(self):
        self.assertContains(self.client.get('/work/notifications/'),'새로운 알림이 없습니다.')
        notice=WorkNotification.objects.create(user=self.user,task=self.task(self.employee,'읽음 확인'),message='배정')
        response=self.client.get('/work/notifications/')
        self.assertTrue(response.context['notification_feed'][0]['unread'])
        action=response.context['notification_feed'][0]['url']
        self.assertEqual(self.client.post(action).status_code,302)
        notice.refresh_from_db();self.assertIsNotNone(notice.read_at)

    def test_completed_pending_notice_is_rendered_without_stale_call_to_action(self):
        notice=self.proposal_notice(self.employee,'승인된 구매 품의',timezone.now())
        notice.kind='pending';notice.message='품의 결재가 필요합니다.';notice.save()
        response=self.client.get('/work/notifications/')
        self.assertContains(response,'승인 완료')
        self.assertContains(response,'처리 결과 보기')
        self.assertNotContains(response,'품의 결재가 필요합니다.')
        row=response.context['notification_feed'][0]
        self.assertTrue(row['closed']);self.assertFalse(row['unread'])
        notice.refresh_from_db()
        self.assertIsNone(notice.read_at)
        self.assertEqual(notice.message,'품의 결재가 필요합니다.')
