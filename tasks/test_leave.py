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
    def test_representative_own_application_blocked(self):
        with self.assertRaises(ValidationError):self.submit(role='executive')
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
    def test_preview_staff_urls_and_post_blocked(self):
        item=self.submit();self.client.force_login(self.users['member'])
        for url in ['/work/leave/','/work/leave/settings/','/work/leave/new/',f'/work/leave/{item.pk}/']:
            self.assertEqual(self.client.get(url).status_code,403)
            self.assertEqual(self.client.post(url,{}).status_code,403)
        response=self.client.get('/work/');self.assertNotContains(response,'휴가 관리 · 준비')
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
