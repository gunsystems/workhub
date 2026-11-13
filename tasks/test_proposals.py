from datetime import date
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied,ValidationError
from django.test import TestCase,override_settings
from .models import Department,Employee,Proposal,WorkDocument,ProposalNotice,StaffEmployment,LeaveYear
from . import proposals,leave


@override_settings(WORKHUB_REQUIRE_HIWORKS=False,WORKHUB_SCHEDULER_ENABLED=False)
class ProposalTests(TestCase):
    def setUp(self):
        self.admin=get_user_model().objects.create_superuser('proposal_admin')
        self.team=Department.objects.create(name='품의검증팀');self.users={};self.people={}
        for role in ['member','leader','executive']:
            user=get_user_model().objects.create_user('proposal_'+role,email=role+'@example.test')
            self.users[role]=user;self.people[role]=Employee.objects.create(user=user,name=role,department=self.team,position=role)
        user=get_user_model().objects.create_user('proposal_other');self.other=user
        Employee.objects.create(user=user,name='다른 직원',department=Department.objects.create(name='다른검증팀'))
    def draft(self,**kwargs):
        item=Proposal.objects.create(employee=self.people['member'],title='검증 품의',category='purchase',body='품목 구입 사유',amount=100,**kwargs)
        WorkDocument.objects.create(proposal=item,name='견적',link='https://example.test/quote',uploader=self.users['member'],uploader_name='member')
        return item
    def act(self,item,role,action='approve',body=''):
        item.refresh_from_db();return proposals.action(item.pk,self.users[role],action,item.revision,body)
    def approve(self,item):
        self.act(item,'member','submit');self.act(item,'leader');return self.act(item,'executive')
    def test_sequential_approval_history_and_badge(self):
        item=self.draft();self.act(item,'member','submit')
        self.assertEqual(proposals.pending_for(self.people['leader']).count(),1)
        with self.assertRaises(PermissionDenied):self.act(item,'executive')
        self.act(item,'leader');self.client.force_login(self.users['leader'])
        self.assertContains(self.client.get('/work/approvals/?tab=history'),item.title)
        self.assertEqual(proposals.pending_for(self.people['executive']).count(),1)
        item=self.act(item,'executive');self.assertEqual(item.state,'approved')
        self.assertFalse(proposals.pending_for(self.people['executive']).exists())
        self.assertTrue(ProposalNotice.objects.filter(event__proposal=item,kind='result',user=self.users['member']).exists())
    def test_rejection_reason_and_stale_approval(self):
        item=self.draft();self.act(item,'member','submit')
        with self.assertRaises(ValidationError):self.act(item,'leader','reject')
        self.act(item,'leader','reject','구매 내역 보완')
        self.act(item,'member','submit');self.act(item,'leader')
        item.refresh_from_db();revision=item.revision;self.act(item,'executive')
        with self.assertRaises(ValidationError):proposals.action(item.pk,self.users['executive'],'approve',revision)
        self.assertEqual(item.events.filter(action='품의 제출').count(),2)
    def test_evidence_required_and_frozen_until_withdrawal(self):
        from .documents import can_add
        item=self.draft();item.documents.all().delete()
        with self.assertRaises(ValidationError):self.act(item,'member','submit')
        WorkDocument.objects.create(proposal=item,name='검증',link='https://example.test/x',uploader_name='member')
        self.act(item,'member','submit');item.refresh_from_db()
        self.assertFalse(can_add(self.users['member'],'proposal',item))
        self.act(item,'member','withdraw');item.refresh_from_db()
        self.assertTrue(can_add(self.users['member'],'proposal',item))
    def test_private_scope_and_document_acl(self):
        item=self.draft();self.client.force_login(self.other)
        self.assertEqual(self.client.get(f'/work/approvals/proposals/{item.pk}/').status_code,404)
        self.assertEqual(self.client.get(f'/work/documents/proposal/{item.pk}/add/').status_code,404)
        self.assertFalse(proposals.visible(self.other).exists())
        self.assertFalse(proposals.visible(self.users['executive']).exists()) # Not a recorded approver yet.
        self.act(item,'member','submit');self.assertTrue(proposals.visible(self.users['executive']).exists())
    def test_claims_cannot_exceed_original_and_payment_is_separate(self):
        original=self.approve(self.draft());first=self.draft(stage='claim',original=original)
        first=self.approve(first);self.assertIsNone(first.paid_at)
        second=self.draft(stage='claim',original=original)
        with self.assertRaises(ValidationError):self.act(second,'member','submit')
        with self.assertRaises(PermissionDenied):self.act(first,'executive','paid','처리')
        result=proposals.action(first.pk,self.admin,'paid',first.revision,'지급 확인 근거')
        self.assertIsNotNone(result.paid_at)
    def test_executive_own_proposal_self_approval(self):
        item=self.draft();item.employee=self.people['executive'];item.save()
        self.act(item,'executive','submit')
        self.assertEqual(self.act(item,'executive').state,'approved')
    def test_form_save_render_and_stale_update_protected(self):
        self.client.force_login(self.users['member'])
        self.assertContains(self.client.get('/work/approvals/proposals/new/'),'사용 전 승인 요청')
        data={'title':'새 품의','category':'education','stage':'prior','body':'자격증 시험','amount':50,'revision':0}
        response=self.client.post('/work/approvals/proposals/new/',data);self.assertEqual(response.status_code,302)
        item=Proposal.objects.get(title='새 품의')
        self.assertContains(self.client.get(response.url),'결재 제출')
        data['revision']=item.revision;self.client.post(f'/work/approvals/proposals/{item.pk}/edit/',data)
        data['title']='오래된 화면 덮어쓰기';response=self.client.post(f'/work/approvals/proposals/{item.pk}/edit/',data)
        self.assertContains(response,'다른 변경이 반영되었습니다.')
        item.refresh_from_db();self.assertEqual(item.title,'새 품의')
    def test_technical_admin_read_only_creation_denied(self):
        item=self.draft();self.client.force_login(self.admin)
        self.assertEqual(self.client.get('/work/approvals/proposals/new/').status_code,403)
        self.assertContains(self.client.get(f'/work/approvals/proposals/{item.pk}/'),'검증 품의')
    def test_approved_leave_calendar_no_duplicate_task_and_cancel_removed(self):
        from .models import Task
        for person in self.people.values():StaffEmployment.objects.create(employee=person,joined_on=date(2020,1,1))
        LeaveYear.objects.create(year=2027,calendar_confirmed=True)
        user=self.users['executive'];self.client.force_login(user)
        item=leave.submit(user,date(2027,1,4),date(2027,1,4),'pm','개인적인 사유 비공개')
        url='/?date=2027-01-04&view=day'
        self.assertNotContains(self.client.get(url),'executive · 오후 반차')
        before=Task.objects.count();leave.change(item.pk,user,'approve','',0)
        response=self.client.get(url);self.assertContains(response,'executive · 오후 반차');self.assertNotContains(response,'개인적인 사유 비공개')
        self.assertEqual(Task.objects.count(),before)
        self.assertContains(self.client.get('/work/approvals/?tab=history&year=2027'),'승인 완료')
        leave.change(item.pk,user,'cancel','일정 변경',1)
        self.assertContains(self.client.get(url),'취소 결재 중')
        leave.change(item.pk,user,'approve','',2)
        self.assertNotContains(self.client.get(url),'executive · 오후 반차')
    def test_failed_delivery_not_marked_sent_and_unverified_skipped(self):
        self.act(self.draft(),'member','submit')
        with patch('accounts.services.deliver') as send:proposals.deliver_pending()
        send.assert_not_called();self.assertEqual(ProposalNotice.objects.get().mail_state,'skipped')

    def test_photo_cache_reuses_bytes_but_rechecks_permission_and_revision(self):
        from django.core.cache import cache
        from django.core.files.base import ContentFile
        from .models import EmployeePhoto
        cache.clear();employee=self.people['member']
        first=EmployeePhoto.objects.create(employee=employee,file='drive/old-photo')
        self.client.force_login(self.users['member'])
        url=f'/accounts/me/photo/{employee.pk}/'
        from django.urls import reverse
        url=reverse('employee_card_photo',args=[employee.pk])
        with patch('tasks.drive_storage.WorkhubStorage._open',side_effect=lambda *a:ContentFile(b'old-jpeg')) as read:
            self.assertEqual(self.client.get(url).content,b'old-jpeg')
            self.assertEqual(self.client.get(url).content,b'old-jpeg');self.assertEqual(read.call_count,1)
            self.client.force_login(self.other);self.assertEqual(self.client.get(url).status_code,403)
            self.assertEqual(read.call_count,1)
        self.client.force_login(self.users['member'])
        EmployeePhoto.objects.create(employee=employee,file='drive/new-photo')
        with patch('tasks.drive_storage.WorkhubStorage._open',side_effect=lambda *a:ContentFile(b'new-jpeg')):
            self.assertEqual(self.client.get(url).content,b'new-jpeg')
        EmployeePhoto.objects.create(employee=employee)
        self.assertEqual(self.client.get(url).status_code,404)

    def test_mail_current_turn_result_and_unknown_outcome(self):
        from accounts.models import AccessProfile
        from django.utils import timezone
        for user in self.users.values():AccessProfile.objects.create(user=user,email=user.email,verified_at=timezone.now())
        item=self.draft();self.act(item,'member','submit');self.act(item,'leader')
        with patch('accounts.services.deliver') as send,patch('accounts.lifecycle.account_allowed',return_value=True):
            proposals.deliver_pending();proposals.deliver_pending()
            self.assertEqual(send.call_count,1);self.assertEqual(send.call_args.args[2],self.users['executive'].email)
            self.assertIn('품의서 확인하기',send.call_args.kwargs['html_body']);self.assertNotIn(item.body,send.call_args.kwargs['html_body'])
        self.act(item,'executive')
        with patch('accounts.services.deliver',side_effect=TimeoutError) as send,patch('accounts.lifecycle.account_allowed',return_value=True):
            proposals.deliver_pending();proposals.deliver_pending();self.assertEqual(send.call_count,1)
        self.assertTrue(ProposalNotice.objects.filter(event__proposal=item,kind='result',mail_state='unknown').exists())
    def test_attachment_cannot_be_archived_while_submitted(self):
        from django.urls import reverse
        item=self.draft();doc=item.documents.get();self.act(item,'member','submit')
        self.client.force_login(self.users['member'])
        self.assertEqual(self.client.post(reverse('document_archive',args=[doc.pk]),{'reason':'변경'}).status_code,403)
        self.act(item,'member','withdraw')
        self.assertEqual(self.client.post(reverse('document_archive',args=[doc.pk]),{'reason':'변경'}).status_code,302)
