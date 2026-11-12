from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import patch, MagicMock
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError, PermissionDenied
from django.test import TestCase, override_settings
from django.utils import timezone
from .models import Department, Employee, StaffEmployment, LeaveYear, LeaveHoliday, LeaveEntry, LeaveRequest, PublicHoliday, HolidaySync, LeaveCalendarReview
from . import leave, holidays

def xml(items,total=None,code='00'):
    body=''.join(f'<item><locdate>{d}</locdate><dateName>{name}</dateName><isHoliday>{flag}</isHoliday></item>' for d,name,flag in items)
    return f'<response><header><resultCode>{code}</resultCode></header><body><totalCount>{len(items) if total is None else total}</totalCount><items>{body}</items></body></response>'.encode()

def calendar(year):
    return {date(year,m,d):'공휴일' for m,d in [(1,1),(2,6),(2,7),(2,8),(2,9),(3,1),(5,1),(5,5),(6,6),(7,17),(8,15),(9,14),(9,15),(9,16),(10,3),(10,9),(12,25)]}

@override_settings(WORKHUB_REQUIRE_HIWORKS=False,WORKHUB_SCHEDULER_ENABLED=False,WORKHUB_HOLIDAY_SYNC_ENABLED=False,PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class HolidayTests(TestCase):
    def setUp(self):
        U=get_user_model();self.admin=U.objects.create_superuser('holiday_admin');self.team=Department.objects.create(name='휴일 검증팀')
        self.users={};self.people={};self.now=timezone.now()
        for role in ['member','leader','executive']:
            u=U.objects.create_user('holiday_'+role);e=Employee.objects.create(user=u,name=role,department=self.team,position=role)
            self.users[role]=u;self.people[role]=e;StaffEmployment.objects.create(employee=e,joined_on=date(2020,1,1))
        LeaveYear.objects.create(year=2027,calendar_confirmed=True)
        LeaveEntry.objects.create(employee=self.people['member'],year=2027,amount=15,reason='초기',actor=self.admin,key='initial')
    def submit(self):return leave.submit(self.users['member'],date(2027,1,4),date(2027,1,5),'annual','휴가')
    def act(self,item,role,action='approve'):
        item.refresh_from_db();return leave.change(item.pk,self.users[role],action,'확인',item.revision)
    def approved(self):
        item=self.submit();self.act(item,'leader');return self.act(item,'executive')
    def test_parse_holiday_flag_duplicate_names_and_nonholiday(self):
        data=holidays.parse_month(xml([('20270101','신정','Y'),('20270101','명칭','Y'),('20270102','기념일','N')]),2027,1)
        self.assertEqual(data,{date(2027,1,1):{'신정','명칭'}})
    def test_reject_partial_wrong_period_bad_status_and_entities(self):
        for data in [xml([('20270101','신정','Y')],total=2),xml([('20260101','신정','Y')]),xml([],code='30'),b'<!DOCTYPE response><response/>',xml([('20270101','신정','?')])]:
            with self.assertRaises(ValueError):holidays.parse_month(data,2027,1)
    def test_fetch_all_months_and_decode_key_only_sent_to_fixed_host(self):
        session=MagicMock();session.__enter__.return_value=session
        records=calendar(2027)
        session.get.side_effect=[MagicMock(status_code=200,content=xml([(day.strftime('%Y%m%d'),name,'Y') for day,name in records.items() if day.month==m])) for m in range(1,13)]
        with patch('tasks.holidays.requests.Session',return_value=session):self.assertEqual(holidays.fetch_year(2027,'sample-key'),records)
        self.assertEqual(session.get.call_count,12)
        self.assertTrue(all(call.args[0]==holidays.API_URL and call.kwargs['allow_redirects'] is False for call in session.get.call_args_list))
    def test_empty_year_never_accepted(self):
        session=MagicMock();session.__enter__.return_value=session;session.get.return_value=MagicMock(status_code=200,content=xml([]))
        with patch('tasks.holidays.requests.Session',return_value=session),self.assertRaises(ValueError):holidays.fetch_year(2027,'key')
    def test_public_holiday_and_company_overlap_excluded_once(self):
        PublicHoliday.objects.create(date=date(2027,1,4),name='임시공휴일')
        LeaveHoliday.objects.create(date=date(2027,1,4),name='회사 휴일')
        self.assertEqual(leave.amount(date(2027,1,4),date(2027,1,5),'annual')[0],1)
    def test_update_retires_removed_official_day_keeps_company_day_and_marks_review(self):
        item=self.submit();PublicHoliday.objects.create(date=date(2027,1,6),name='이전 휴일')
        LeaveHoliday.objects.create(date=date(2027,1,7),name='회사 휴일')
        holidays.apply_year(2027,{date(2027,1,4):'신규 휴일'},self.now)
        self.assertFalse(PublicHoliday.objects.get(date=date(2027,1,6)).active)
        self.assertTrue(LeaveHoliday.objects.filter(date=date(2027,1,7)).exists())
        item.refresh_from_db();self.assertEqual(item.days,2)
        self.assertTrue(item.calendar_review.pending)
        with self.assertRaises(ValidationError):self.act(item,'leader')
    def test_pending_reconcile_restarts_approvals_and_stale_submit_rejected(self):
        item=self.submit();self.act(item,'leader')
        holidays.apply_year(2027,{date(2027,1,4):'임시공휴일'},self.now)
        item.refresh_from_db();revision=item.revision;holidays.reconcile(item.pk,self.admin,revision)
        item.refresh_from_db();self.assertEqual(item.days,1);self.assertEqual(leave.current(item).role,'leader')
        self.assertEqual(item.steps.filter(round=0,decision='approve').count(),1)
        self.assertEqual(leave.balance(item.employee,2027),15)
        with self.assertRaises(ValidationError):holidays.reconcile(item.pk,self.admin,revision)
    def test_approved_reconcile_refunds_delta_once_and_cancellation_restores_remainder(self):
        item=self.approved();self.assertEqual(leave.balance(item.employee,2027),13)
        holidays.apply_year(2027,{date(2027,1,4):'임시공휴일'},self.now)
        item.refresh_from_db();holidays.reconcile(item.pk,self.admin,item.revision)
        self.assertEqual(leave.balance(item.employee,2027),14)
        self.act(item,'member','cancel');self.act(item,'leader');self.act(item,'executive')
        self.assertEqual(leave.balance(item.employee,2027),15)
        self.assertEqual(LeaveEntry.objects.filter(key__startswith='holiday:').count(),1)
    def test_removed_holiday_can_increase_days_with_audited_adjustment(self):
        PublicHoliday.objects.create(date=date(2027,1,4),name='기존 휴일');item=self.approved()
        holidays.apply_year(2027,{},self.now);item.refresh_from_db();holidays.reconcile(item.pk,self.admin,item.revision)
        self.assertEqual(leave.balance(item.employee,2027),13)
        self.assertTrue(item.events.filter(action='휴무일 변경 반영').exists())
    def test_all_days_become_holidays_pending_withdrawn_approved_zero_refunded(self):
        item=self.submit();holidays.apply_year(2027,{date(2027,1,4):'휴일',date(2027,1,5):'휴일'},self.now)
        holidays.reconcile(item.pk,self.admin,item.revision);item.refresh_from_db();self.assertEqual(item.state,'withdrawn');self.assertEqual(item.days,0)
        PublicHoliday.objects.update(active=False)
        approved=self.approved();holidays.apply_year(2027,{date(2027,1,4):'휴일',date(2027,1,5):'휴일'},self.now)
        holidays.reconcile(approved.pk,self.admin,approved.revision);approved.refresh_from_db()
        self.assertEqual(approved.days,0);self.assertEqual(leave.balance(approved.employee,2027),15)
    def test_reconcile_is_administrator_only(self):
        item=self.submit();holidays.apply_year(2027,{date(2027,1,4):'휴일'},self.now)
        with self.assertRaises(PermissionDenied):holidays.reconcile(item.pk,self.users['executive'],item.revision)
    def test_company_holiday_addition_flags_affected_request(self):
        item=self.submit();self.client.force_login(self.admin)
        response=self.client.post('/work/leave/settings/?year=2027',{'action':'holiday','date':'2027-01-04','name':'회사 휴일'})
        self.assertEqual(response.status_code,302);self.assertTrue(LeaveCalendarReview.objects.get(request=item).pending)
    @override_settings(WORKHUB_HOLIDAY_SYNC_ENABLED=True,KASI_SERVICE_KEY='')
    def test_missing_key_does_not_call_network_or_report_success(self):
        with patch('tasks.holidays.fetch_year') as fetch:holidays.synchronize_due()
        fetch.assert_not_called();self.assertEqual(set(HolidaySync.objects.values_list('state',flat=True)),{'configuration'})
        self.assertFalse(PublicHoliday.objects.exists())
    @override_settings(WORKHUB_HOLIDAY_SYNC_ENABLED=True,KASI_SERVICE_KEY='not-real-secret')
    def test_success_daily_idempotence_and_year_rollover_creation(self):
        with patch('tasks.holidays.fetch_year',side_effect=lambda y,k:calendar(y)) as fetch:
            holidays.synchronize_due();holidays.synchronize_due()
        self.assertEqual(fetch.call_count,2);self.assertEqual(HolidaySync.objects.filter(state='ready').count(),2)
        self.assertEqual(PublicHoliday.objects.count(),34)
    @override_settings(WORKHUB_HOLIDAY_SYNC_ENABLED=True,KASI_SERVICE_KEY='not-real-secret')
    def test_network_failure_retains_cache_and_never_persists_secret(self):
        PublicHoliday.objects.create(date=date(2027,1,4),name='기존 휴일')
        with patch('tasks.holidays.fetch_year',side_effect=RuntimeError('URL?ServiceKey=not-real-secret')):holidays.synchronize_due()
        self.assertTrue(PublicHoliday.objects.get(date=date(2027,1,4)).active)
        self.assertFalse(HolidaySync.objects.exclude(last_success=None).exists())
        self.assertFalse(HolidaySync.objects.filter(message__contains='not-real-secret').exists())
    @override_settings(WORKHUB_HOLIDAY_SYNC_ENABLED=True,KASI_SERVICE_KEY='key')
    def test_live_lease_skips_duplicate_worker(self):
        year=timezone.localdate().year
        for y in [year,year+1]:HolidaySync.objects.create(year=y,lease_until=self.now+timedelta(hours=1))
        with patch('tasks.holidays.fetch_year') as fetch:holidays.synchronize_due()
        fetch.assert_not_called()
    @override_settings(WORKHUB_HOLIDAY_SYNC_ENABLED=True)
    def test_stale_calendar_blocks_new_request_and_confirmation(self):
        with self.assertRaises(ValidationError):self.submit()
        HolidaySync.objects.create(year=2027,last_success=timezone.now(),state='ready')
        self.assertEqual(self.submit().days,2)
        self.client.force_login(self.admin)
        response=self.client.post('/work/leave/settings/?year=2028',{'action':'year','year':'2028','calendar_confirmed':'on','note':''})
        self.assertEqual(response.status_code,200);self.assertFalse(LeaveYear.objects.filter(year=2028).exists())
    def test_settings_ui_separates_automatic_and_company_holidays(self):
        self.client.force_login(self.admin);response=self.client.get('/work/leave/settings/?year=2027')
        self.assertContains(response,'공휴일 자동 연결');self.assertContains(response,'회사 지정 휴일 추가');self.assertNotContains(response,'법정 공휴일은 자동으로 입력되지 않습니다.')
        self.assertEqual(self.client.post('/work/leave/settings/?year=2027',{'action':'review_holidays','request_id':'999','revision':'0'}).status_code,409)

    def test_employee_calendar_displays_holidays_in_month_week_day_without_task_counts(self):
        PublicHoliday.objects.create(date=date(2027,1,4),name='임시공휴일')
        PublicHoliday.objects.create(date=date(2027,1,5),name='이전 공휴일',active=False)
        LeaveHoliday.objects.create(date=date(2027,1,4),name='회사 창립일')
        self.client.force_login(self.users['member'])
        for mode in ['month','week','day']:
            response=self.client.get('/?view='+mode+'&date=2027-01-04')
            self.assertContains(response,'임시공휴일');self.assertContains(response,'회사 창립일 · 회사 휴일')
            self.assertNotContains(response,'이전 공휴일')
            self.assertEqual(response.context['count'],0)
            day=next(d for d in response.context['days'] if d['date']==date(2027,1,4))
            self.assertEqual(day['total'],0)
            self.assertNotContains(response,'휴가 신청 사유')
