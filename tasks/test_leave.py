from datetime import date
from decimal import Decimal
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError,PermissionDenied
from django.test import TestCase,override_settings
from .models import Department,Employee,StaffEmployment,LeaveYear,LeaveHoliday,LeaveEntry,LeaveRequest
from . import leave

@override_settings(WORKHUB_REQUIRE_HIWORKS=False,WORKHUB_SCHEDULER_ENABLED=False,PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class LeaveTests(TestCase):
    def setUp(self):
        U=get_user_model();self.admin=U.objects.create_superuser('leave_admin')
        self.team=Department.objects.create(name='휴가검증팀')
        self.people={};self.users={}
        for role in ['member','leader','executive']:
            u=U.objects.create_user('leave_'+role);e=Employee.objects.create(name=role,user=u,department=self.team,position=role)
            StaffEmployment.objects.create(employee=e,joined_on=date(2020,1,1));self.people[role]=e;self.users[role]=u
        LeaveYear.objects.create(year=2027,calendar_confirmed=True)
        LeaveEntry.objects.create(employee=self.people['member'],year=2027,amount=15,actor=self.admin,reason='초기 부여',key='initial')
    def submit(self,kind='annual',start=date(2027,1,4),end=date(2027,1,4),role='member'):
        return leave.submit(self.users[role],start,end,kind,'개인 휴가')
    def act(self,item,role,action='approve',reason='확인했습니다.'):
        item.refresh_from_db();return leave.change(item.pk,self.users[role],action,reason,item.revision)
    def test_charge_only_after_final_approval_and_head_delegation(self):
        item=self.submit();self.assertEqual(leave.reserved(item.employee,2027),1)
        self.assertEqual(list(item.steps.values_list('role',flat=True)),['leader','head','executive'])
        self.act(item,'leader');self.assertEqual(leave.balance(item.employee,2027),15)
        item=self.act(item,'executive');self.assertEqual(item.state,'approved');self.assertEqual(leave.balance(item.employee,2027),14)
        self.assertTrue(item.steps.get(role='head').delegated);self.assertEqual(item.steps.filter(decision='approve').count(),3)
    def test_out_of_order_and_self_approval_denied(self):
        item=self.submit()
        for role in ['executive','member']:
            with self.assertRaises(PermissionDenied):self.act(item,role)
    def test_cancel_requires_approval_and_restores_once(self):
        item=self.submit();self.act(item,'leader');self.act(item,'executive');self.act(item,'member','cancel','일정 변경')
        self.assertEqual(leave.balance(item.employee,2027),14)
        self.act(item,'leader');item=self.act(item,'executive');self.assertEqual(item.state,'cancelled');self.assertEqual(leave.balance(item.employee,2027),15)
        with self.assertRaises(PermissionDenied):self.act(item,'executive')
        self.assertEqual(LeaveEntry.objects.filter(key__startswith='leave:').count(),2)
    def test_cancellation_can_be_requested_again_after_rejection(self):
        item=self.submit();self.act(item,'leader');self.act(item,'executive');self.act(item,'member','cancel');self.act(item,'leader','reject')
        self.act(item,'member','cancel');self.act(item,'leader');item=self.act(item,'executive');self.assertEqual(item.state,'cancelled')
    def test_rejection_and_withdraw_do_not_charge(self):
        item=self.submit();self.act(item,'leader','reject');item2=self.submit();self.act(item2,'member','withdraw')
        self.assertEqual(leave.balance(item.employee,2027),15);self.assertEqual(leave.reserved(item.employee,2027),0)
    def test_stale_post_cannot_double_approve(self):
        item=self.submit();self.act(item,'leader')
        with self.assertRaises(ValidationError):leave.change(item.pk,self.users['leader'],'approve','다시',0)
    def test_head_registered_chain_is_frozen(self):
        item=self.submit();u=get_user_model().objects.create_user('head');head=Employee.objects.create(user=u,name='head',department=self.team,position='head')
        self.assertTrue(item.steps.get(role='head').delegated)
        other=self.submit(start=date(2027,1,5),end=date(2027,1,5));self.assertEqual(other.steps.get(role='head').approver,head)
    def test_leader_skips_leader_slot(self):
        item=self.submit(role='leader');self.assertEqual(list(item.steps.values_list('role',flat=True)),['head','executive'])
    def test_representative_own_application_self_approval(self):
        item=self.submit(role='executive')
        self.assertEqual(list(item.steps.values_list('role',flat=True)),['executive'])
        self.assertEqual(leave.pending_for(self.people['executive']).count(),1)
        self.act(item,'executive')
        self.assertEqual(leave.balance(self.people['executive'],2027),-1)
        self.act(item,'executive','cancel');self.act(item,'executive')
        self.assertEqual(leave.balance(self.people['executive'],2027),0)
    def test_missing_or_multiple_approvers_block(self):
        self.people['leader'].is_active=False;self.people['leader'].save()
        with self.assertRaises(ValidationError):self.submit()
    def test_weekend_holiday_half_and_cross_year(self):
        LeaveHoliday.objects.create(date=date(2027,1,4),name='확인 휴무')
        value,days=leave.amount(date(2027,1,1),date(2027,1,5),'annual');self.assertEqual(value,2)
        self.assertEqual(leave.amount(date(2027,1,5),date(2027,1,5),'am')[0],Decimal('.5'))
        for start,end,kind in [(date(2027,1,2),date(2027,1,3),'annual'),(date(2027,12,31),date(2028,1,1),'annual'),(date(2027,1,5),date(2027,1,6),'am')]:
            with self.assertRaises(ValidationError):leave.amount(start,end,kind)
    def test_overlap_and_complementary_half_days(self):
        self.submit('am');self.submit('pm')
        with self.assertRaises(ValidationError):self.submit()
        with self.assertRaises(ValidationError):self.submit('am')
    def test_excess_allowed_with_negative_ledger(self):
        LeaveEntry.objects.create(employee=self.people['member'],year=2027,amount=-15,actor=self.admin,reason='초기 조정',key='adjust')
        item=self.submit();self.act(item,'leader');self.act(item,'executive');self.assertEqual(leave.balance(item.employee,2027),-1)
    def test_unconfirmed_calendar_blocks(self):
        LeaveYear.objects.update(calendar_confirmed=False)
        with self.assertRaises(ValidationError):self.submit()
    def test_missing_join_date_blocks(self):
        StaffEmployment.objects.filter(employee=self.people['member']).delete()
        with self.assertRaises(ValidationError):self.submit()
    def test_private_scope_other_department_hidden(self):
        item=self.submit();u=get_user_model().objects.create_user('stranger');team=Department.objects.create(name='다른팀');Employee.objects.create(user=u,name='stranger',department=team)
        self.assertFalse(leave.visible(u).exists());self.assertTrue(leave.visible(self.users['leader']).filter(pk=item.pk).exists())
    def test_staff_leave_open_but_administration_blocked(self):
        item=self.submit();self.client.force_login(self.users['member'])
        for url in ['/work/leave/','/work/leave/settings/','/work/leave/preparation/']:
            self.assertEqual(self.client.get(url).status_code,403)
            self.assertEqual(self.client.post(url,{}).status_code,403)
        for url in ['/work/leave/new/',f'/work/leave/{item.pk}/','/work/approvals/']:
            self.assertEqual(self.client.get(url).status_code,200)
        self.assertContains(self.client.get('/work/approvals/'),'href="/work/leave/new/"')

    def test_admin_preview_renders_and_navigation(self):
        item=self.submit();self.client.force_login(self.admin)
        for url in ['/work/leave/','/work/leave/settings/','/work/leave/new/',f'/work/leave/{item.pk}/']:
            response=self.client.get(url)
            self.assertEqual(response.status_code,403 if url.endswith('/new/') else 200)
        self.assertContains(self.client.get('/work/leave/'),'관리자 미리보기')
    def test_admin_technical_account_cannot_approve(self):
        item=self.submit()
        with self.assertRaises(PermissionDenied):leave.change(item.pk,self.admin,'approve','관리자',0)
    def test_admin_grant_idempotent(self):
        import uuid
        self.client.force_login(self.admin);data={'action':'grant','employee':self.people['member'].pk,'year':2027,'amount':'1.5','reason':'추가 부여','key':str(uuid.uuid4())}
        self.assertEqual(self.client.post('/work/leave/settings/',data).status_code,302)
        self.assertEqual(self.client.post('/work/leave/settings/',data).status_code,302)
        self.assertEqual(leave.balance(self.people['member'],2027),Decimal('16.5'))
    def test_no_year_rollover_automatic_grants(self):
        self.client.force_login(self.admin);self.client.get('/work/leave/?year=2028');self.assertEqual(LeaveEntry.objects.filter(year=2028).count(),0)

    def test_leave_is_nested_under_staff_management_navigation(self):
        self.client.force_login(self.admin)
        response=self.client.get('/work/leave/settings/')
        self.assertEqual(response.status_code,200)
        groups=response.context['nav_groups']
        entries=[item for group in groups for item in group['items']]
        self.assertFalse(any(item['href']=='/work/leave/' for item in entries))
        parent=next(item for item in entries if item['href']=='/accounts/employees/')
        self.assertTrue(parent['active'])
        self.assertEqual(response.context['staff_management_section'],'employment')
        self.assertContains(response,'aria-label="직원 관리 메뉴"')
        self.assertContains(response,'href="/work/leave/settings/" class="is-current" aria-current="page"')

    def test_preparation_is_read_only_admin_preview(self):
        before=(LeaveEntry.objects.count(),LeaveRequest.objects.count(),LeaveYear.objects.count())
        self.client.force_login(self.admin)
        response=self.client.get('/work/leave/preparation/?year=2027')
        self.assertEqual(response.status_code,200)
        self.assertContains(response,'휴가 운영 준비 점검')
        member=next(row for row in response.context['rows'] if row['employee']==self.people['member'])
        self.assertEqual(member['balance'],15)
        self.assertEqual(member['issues'],[])
        self.assertEqual([role for role,person,delegated in member['chain']],['leader','head','executive'])
        self.assertEqual(self.client.post('/work/leave/preparation/',{}).status_code,405)
        self.assertEqual(before,(LeaveEntry.objects.count(),LeaveRequest.objects.count(),LeaveYear.objects.count()))
        for role in ['member','leader','executive']:
            self.client.force_login(self.users[role])
            self.assertEqual(self.client.get('/work/leave/preparation/').status_code,403)

    def test_preparation_identifies_missing_join_date_grant_and_team_leader(self):
        StaffEmployment.objects.filter(employee=self.people['member']).delete()
        LeaveEntry.objects.all().delete()
        self.people['leader'].is_active=False;self.people['leader'].save()
        row=next(r for r in leave.preparation(2027)['rows'] if r['employee']==self.people['member'])
        self.assertIn('입사일 등록 필요',row['issues'])
        self.assertIn('연차 부여·적용 대상 확인 필요',row['issues'])
        self.assertTrue(any('팀장' in issue for issue in row['issues']))

    def test_preparation_ledger_handles_charge_restore_and_manual_adjustment(self):
        item=self.submit();self.act(item,'leader');self.act(item,'executive')
        row=next(r for r in leave.preparation(2027)['rows'] if r['employee']==item.employee)
        self.assertEqual((row['granted'],row['used'],row['balance']),(15,1,14))
        self.act(item,'member','cancel');self.act(item,'leader');self.act(item,'executive')
        LeaveEntry.objects.create(employee=item.employee,year=2027,amount=-2,actor=self.admin,reason='관리자 조정',key='grant:adjustment')
        row=next(r for r in leave.preparation(2027)['rows'] if r['employee']==item.employee)
        self.assertEqual((row['granted'],row['used'],row['balance']),(13,0,13))

    def test_head_own_leave_uses_only_representative_approval(self):
        u=get_user_model().objects.create_user('leave_head')
        head=Employee.objects.create(user=u,name='head',department=self.team,position='head')
        StaffEmployment.objects.create(employee=head,joined_on=date(2020,1,1))
        item=leave.submit(u,date(2027,1,4),date(2027,1,4),'annual','개인 휴가')
        self.assertEqual(list(item.steps.values_list('role',flat=True)),['executive'])
        self.act(item,'executive');self.assertEqual(leave.balance(head,2027),-1)

    def test_actual_head_separate_approval_and_cancellation_rejection(self):
        u=get_user_model().objects.create_user('active_head')
        head=Employee.objects.create(user=u,name='head',department=self.team,position='head')
        item=self.submit();self.act(item,'leader')
        with self.assertRaises(PermissionDenied):self.act(item,'executive')
        item.refresh_from_db();leave.change(item.pk,u,'approve','검토 완료',item.revision)
        self.assertEqual(leave.balance(item.employee,2027),15)
        self.act(item,'executive');self.act(item,'member','cancel');self.act(item,'leader','reject')
        self.assertEqual(leave.balance(item.employee,2027),14)
        self.assertEqual(leave.reserved(item.employee,2027),0)

    def test_half_day_deduction_restoration_and_negative_balance(self):
        item=self.submit('am');self.act(item,'leader');self.act(item,'executive')
        self.assertEqual(leave.balance(item.employee,2027),Decimal('14.5'))
        self.act(item,'member','cancel');self.act(item,'leader');self.act(item,'executive')
        self.assertEqual(leave.balance(item.employee,2027),15)

    def test_preparation_does_not_claim_unregistered_calendar_is_ready(self):
        data=leave.preparation(2028)
        self.assertFalse(data['calendar_ready'])
        self.assertEqual(data['holiday_count'],0)
        self.assertFalse(LeaveYear.objects.filter(year=2028).exists())

    def test_preparation_representative_excluded_from_application_route(self):
        row=next(r for r in leave.preparation(2027)['rows'] if r['representative'])
        self.assertFalse(row['chain'])
        self.assertFalse(any('대표 본인' in issue for issue in row['issues']))

    def test_settings_form_labels_target_distinct_controls(self):
        import re
        self.client.force_login(self.admin)
        response=self.client.get('/work/leave/settings/?year=2027')
        html=response.content.decode()
        ids=re.findall(r'\bid="([^"]+)"',html)
        self.assertEqual(len(ids),len(set(ids)))
        self.assertContains(response,'for="leave_employment_employee"')
        self.assertContains(response,'for="leave_grant_employee"')
        self.assertContains(response,'for="leave_year_year"')
        self.assertNotContains(response,'Employee:')

    def test_entitlement_reference_tenure_cap_and_midyear(self):
        person=self.people['member']
        employment=person.employment
        for joined, expected in [(date(2020,1,1),18),(date(2024,1,1),16),(date(2024,5,13),15),(date(1990,1,1),25)]:
            employment.joined_on=joined;employment.save()
            self.assertEqual(leave.entitlement_preview(person,2027)['days'],expected)
        employment.joined_on=date(2026,5,1);employment.save()
        self.assertIsNone(leave.entitlement_preview(person,2027)['days'])
        self.assertIsNone(leave.entitlement_preview(self.people['executive'],2027)['days'])

    def test_selected_grant_prefills_only_without_existing_ledger(self):
        self.client.force_login(self.admin)
        url=f'/work/leave/settings/?year=2027&employee={self.people["member"].pk}'
        response=self.client.get(url)
        self.assertEqual(response.context['grant_form'].initial['employee'],self.people['member'].pk)
        self.assertNotIn('amount',response.context['grant_form'].initial)
        LeaveEntry.objects.all().delete()
        response=self.client.get(url)
        self.assertEqual(response.context['grant_form'].initial['amount'],18)
        self.assertFalse(LeaveEntry.objects.exists())

    def test_inactive_employee_cannot_decide_even_with_active_login(self):
        item=self.submit()
        self.people['leader'].is_active=False;self.people['leader'].save()
        with self.assertRaises(PermissionDenied):self.act(item,'leader')
        item.refresh_from_db();self.assertEqual(item.revision,0)

    def test_overview_separates_grants_usage_pending_and_balance(self):
        item=self.submit();self.act(item,'leader');self.act(item,'executive')
        self.submit(start=date(2027,1,5),end=date(2027,1,5))
        self.client.force_login(self.admin)
        response=self.client.get('/work/leave/?year=2027')
        row=next(r for r in response.context['rows'] if r['employee']==item.employee)
        self.assertEqual((row['granted'],row['used'],row['balance'],row['reserved']),(15,1,14,1))
        self.assertContains(response,'부여·조정')
        self.assertContains(response,'연차 확인')

    def test_holiday_reconciliation_is_usage_adjustment_not_new_grant(self):
        from .models import LeaveCalendarReview
        from .holidays import reconcile
        item=self.submit(start=date(2027,1,4),end=date(2027,1,5))
        self.act(item,'leader');self.act(item,'executive')
        LeaveHoliday.objects.create(date=date(2027,1,5),name='회사 휴무')
        LeaveCalendarReview.objects.create(request=item)
        item.refresh_from_db();reconcile(item.pk,self.admin,item.revision)
        row=next(r for r in leave.preparation(2027)['rows'] if r['employee']==item.employee)
        self.assertEqual((row['granted'],row['used'],row['balance']),(15,1,14))
        self.act(item,'member','cancel');self.act(item,'leader');self.act(item,'executive')
        row=next(r for r in leave.preparation(2027)['rows'] if r['employee']==item.employee)
        self.assertEqual((row['granted'],row['used'],row['balance']),(15,0,15))

    def test_registered_leader_without_account_is_previewed_but_cannot_approve(self):
        person=self.people['leader'];person.user=None;person.save()
        chain=leave.route(self.people['member'],require_account=False)
        self.assertEqual(chain[0][1],person)
        with self.assertRaises(ValidationError):self.submit()
        row=next(r for r in leave.preparation(2027)['rows'] if r['employee']==self.people['member'])
        self.assertTrue(any('결재 계정 인증 완료 필요' in issue for issue in row['issues']))
        self.assertFalse(LeaveRequest.objects.exists())

    def test_registered_head_without_account_is_not_treated_as_vacant(self):
        Employee.objects.create(name='미인증 본부장',department=self.team,position='head')
        with self.assertRaises(ValidationError):self.submit()
        chain=leave.route(self.people['member'],require_account=False)
        self.assertEqual(chain[1][0],'head');self.assertFalse(chain[1][2])

    def test_portal_open_and_admin_selection_read_only(self):
        self.client.force_login(self.users['member'])
        self.assertEqual(self.client.get('/work/approvals/').status_code,200)
        self.assertContains(self.client.get('/dashboard/'),'href="/work/approvals/"')
        self.assertNotContains(self.client.get('/work/approvals/'),'미리볼 직원')
        self.assertEqual(self.client.get('/work/approvals/?employee='+str(self.people['executive'].pk)).status_code,403)
        self.client.force_login(self.admin)
        self.assertEqual(self.client.post('/work/approvals/',{}).status_code,405)
        self.assertContains(self.client.get('/work/approvals/'),'미리볼 직원을 선택해 주세요.')
        self.assertContains(self.client.get('/work/approvals/?employee='+str(self.people['member'].pk)),'disabled')

    def test_portal_preview_filters_current_approval_and_keeps_balances(self):
        item=self.submit();before=LeaveEntry.objects.count()
        self.client.force_login(self.admin)
        url='/work/approvals/?year=2027&tab=inbox&employee='
        response=self.client.get(url+str(self.people['leader'].pk))
        self.assertEqual([r.pk for r in response.context['pending_items']],[item.pk])
        self.assertEqual(self.client.get(url+str(self.people['executive'].pk)).context['pending_items'],[])
        self.act(item,'leader')
        self.assertEqual(self.client.get(url+str(self.people['leader'].pk)).context['pending_items'],[])
        self.assertEqual([r.pk for r in self.client.get(url+str(self.people['executive'].pk)).context['pending_items']],[item.pk])
        self.assertEqual(LeaveEntry.objects.count(),before)

    def test_portal_calendar_excludes_unapproved_and_private_reason(self):
        item=self.submit();self.client.force_login(self.admin)
        url=f'/work/approvals/?year=2027&tab=calendar&employee={self.people["member"].pk}'
        self.assertEqual(list(self.client.get(url).context['calendar_items']),[])
        self.act(item,'leader');self.act(item,'executive')
        response=self.client.get(url)
        self.assertEqual([r.pk for r in response.context['calendar_items']],[item.pk])
        self.assertNotContains(response,'개인 휴가')
        self.assertNotContains(response,'>신청 내용</a>')

    def test_approval_count_tracks_current_step_and_cancellation_round(self):
        first=self.submit();second=self.submit(start=date(2027,1,5),end=date(2027,1,5))
        self.assertEqual(leave.pending_for(self.people['leader']).count(),2)
        self.assertEqual(leave.pending_for(self.people['executive']).count(),0)
        self.act(first,'leader');self.act(second,'leader')
        self.assertEqual(leave.pending_for(self.people['leader']).count(),0)
        self.assertEqual(leave.pending_for(self.people['executive']).count(),2)
        self.act(first,'executive')
        self.assertEqual(leave.pending_for(self.people['executive']).count(),1)
        self.act(first,'member','cancel')
        self.assertEqual(leave.pending_for(self.people['leader']).count(),1)
        self.assertEqual(leave.pending_for(self.people['executive']).count(),1)
        self.act(first,'leader')
        self.assertEqual(leave.pending_for(self.people['executive']).count(),2)
        self.act(first,'executive')
        self.assertEqual(leave.pending_for(self.people['executive']).count(),1)

    def test_sidebar_count_uses_signed_in_person_not_preview_selection(self):
        from django.test import RequestFactory
        from .navigation import navigation
        item=self.submit();self.act(item,'leader')
        request=RequestFactory().get('/work/approvals/?employee='+str(self.people['leader'].pk))
        request.user=self.users['executive']
        self.assertEqual(navigation(request)['approval_pending_count'],1)
        request.user=self.admin
        self.assertEqual(navigation(request)['approval_pending_count'],0)
        self.users['executive'].is_superuser=True;self.users['executive'].save()
        self.client.force_login(self.users['executive'])
        self.assertContains(self.client.get('/work/approvals/'),'aria-label="결재 대기 1건"')
        self.act(item,'executive')
        self.assertNotContains(self.client.get('/work/approvals/'),'aria-label="결재 대기 1건"')

    @override_settings(WORKHUB_LEAVE_NOTIFICATIONS_ENABLED=False)
    def test_leave_notices_can_be_disabled(self):
        from .models import LeaveNotice
        self.submit()
        self.assertFalse(LeaveNotice.objects.exists())

    @override_settings(WORKHUB_LEAVE_NOTIFICATIONS_ENABLED=True)
    def test_leave_mail_waits_for_turn_and_final_result_to_applicant(self):
        from .models import LeaveNotice
        from .leave_notifications import deliver_pending
        from accounts.models import AccessProfile
        from django.utils import timezone
        from unittest.mock import patch
        for role,user in self.users.items():
            user.email=role+'@gunsystems.co.kr';user.save()
            AccessProfile.objects.create(user=user,email=user.email,verified_at=timezone.now())
        item=self.submit()
        self.assertEqual(list(LeaveNotice.objects.values_list('user_id',flat=True)),[self.users['leader'].pk])
        self.act(item,'leader')
        with patch('accounts.services.deliver') as send,patch('accounts.lifecycle.account_allowed',return_value=True):
            deliver_pending();deliver_pending()
            self.assertEqual(send.call_count,1)
            self.assertEqual(send.call_args.args[2],self.users['executive'].email)
            html=send.call_args.kwargs['html_body']
            self.assertIn('결재 내용 확인',html)
            self.assertIn('/work/leave/'+str(item.pk)+'/',html)
            self.assertNotIn('개인 휴가',html)
        self.assertEqual(LeaveNotice.objects.get(user=self.users['leader']).mail_state,'skipped')
        self.act(item,'executive')
        with patch('accounts.services.deliver') as send,patch('accounts.lifecycle.account_allowed',return_value=True):
            deliver_pending();self.assertEqual(send.call_count,1)
            self.assertEqual(send.call_args.args[2],self.users['member'].email)
            self.assertIn('최종 승인',send.call_args.args[0])
        self.act(item,'member','cancel');self.act(item,'leader');self.act(item,'executive')
        self.assertTrue(LeaveNotice.objects.filter(user=self.users['member'],kind='result',message='휴가 취소가 승인되었습니다.').exists())

    @override_settings(WORKHUB_LEAVE_NOTIFICATIONS_ENABLED=True)
    def test_leave_mail_changed_email_skipped_and_unknown_not_retried(self):
        from .models import LeaveNotice
        from .leave_notifications import deliver_pending
        from accounts.models import AccessProfile
        from django.utils import timezone
        from unittest.mock import patch
        user=self.users['leader'];user.email='leader@gunsystems.co.kr';user.save()
        AccessProfile.objects.create(user=user,email=user.email,verified_at=timezone.now())
        first=self.submit()
        with patch('accounts.services.deliver',side_effect=TimeoutError) as send,patch('accounts.lifecycle.account_allowed',return_value=True):
            deliver_pending();deliver_pending();self.assertEqual(send.call_count,1)
        self.assertEqual(LeaveNotice.objects.get(event__request=first).mail_state,'unknown')
        second=self.submit(start=date(2027,1,5),end=date(2027,1,5))
        user.email='changed@gunsystems.co.kr';user.save()
        with patch('accounts.services.deliver') as send,patch('accounts.lifecycle.account_allowed',return_value=True):
            deliver_pending();send.assert_not_called()
        self.assertEqual(LeaveNotice.objects.get(event__request=second).mail_state,'skipped')

    def test_portal_executive_annual_summary_and_member_privacy(self):
        self.client.force_login(self.admin)
        url='/work/approvals/?year=2027&tab=calendar&employee='
        response=self.client.get(url+str(self.people['executive'].pk))
        self.assertEqual(len(response.context['annual_rows']),3)
        self.assertTrue(all('reserved' in row for row in response.context['annual_rows']))
        self.assertContains(response,'2027년 직원별 연차 현황')
        self.assertEqual(self.client.get(url+str(self.people['member'].pk)).context['annual_rows'],[])

    def test_employee_http_application_leader_and_executive_review(self):
        self.client.force_login(self.users['member'])
        response=self.client.post('/work/leave/new/',{'kind':'annual','start':'2027-01-04','end':'2027-01-04','reason':'화면 검증'})
        self.assertEqual(response.status_code,302)
        item=LeaveRequest.objects.get(reason='화면 검증')
        url=f'/work/leave/{item.pk}/'
        self.assertEqual(self.client.post(url,{'action':'approve','revision':0,'body':'본인 승인 불가'}).status_code,403)
        self.client.force_login(self.users['executive'])
        self.assertEqual(self.client.post(url,{'action':'approve','revision':0,'body':'선행 승인 불가'}).status_code,403)
        self.client.force_login(self.users['leader'])
        self.assertEqual(self.client.post(url,{'action':'approve','revision':0,'body':'팀장 확인'}).status_code,302)
        self.client.force_login(self.users['executive'])
        self.assertEqual(self.client.post(url,{'action':'approve','revision':1,'body':'최종 확인'}).status_code,302)
        item.refresh_from_db();self.assertEqual(item.state,'approved')
        self.assertEqual(leave.balance(self.people['member'],2027),14)

    def test_representative_http_self_approval_and_private_scope(self):
        self.client.force_login(self.users['executive'])
        self.assertEqual(self.client.post('/work/leave/new/',{'kind':'annual','start':'2027-01-05','end':'2027-01-05','reason':'대표 직접 신청'}).status_code,302)
        item=LeaveRequest.objects.get(reason='대표 직접 신청')
        self.assertContains(self.client.get(f'/work/leave/{item.pk}/'),'대표 본인 휴가는 신청 후 본인이 승인합니다.')
        self.assertEqual(self.client.post(f'/work/leave/{item.pk}/',{'action':'approve','revision':0,'body':'본인 확인'}).status_code,302)
        self.client.force_login(self.users['member'])
        self.assertEqual(self.client.get(f'/work/leave/{item.pk}/').status_code,404)
        self.assertNotContains(self.client.get('/work/approvals/?tab=calendar&year=2027'),'대표 직접 신청')

    def test_employee_notice_is_visible_only_to_recipient(self):
        from .models import LeaveNotice
        item=self.submit()
        self.client.force_login(self.users['leader'])
        self.assertContains(self.client.get('/work/notifications/'),'휴가 신청 결재가 필요합니다.')
        self.client.get(f'/work/leave/{item.pk}/')
        self.assertTrue(LeaveNotice.objects.get(user=self.users['leader'],event__request=item).read_at)
        self.client.force_login(self.users['member'])
        self.assertNotContains(self.client.get('/work/notifications/'),'휴가 신청 결재가 필요합니다.')
