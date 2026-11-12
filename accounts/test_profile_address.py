from datetime import date
import json
from django.test import TestCase,override_settings,Client
from django.contrib.auth import get_user_model
from tasks.models import Employee,Department,StaffEmployment,EmployeeAddress,SystemAudit
from .profile_views import tenure_text,AddressForm

@override_settings(WORKHUB_REQUIRE_HIWORKS=False,WORKHUB_SCHEDULER_ENABLED=False,PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class ProfileAddressTests(TestCase):
    def setUp(self):
        U=get_user_model();self.a=U.objects.create_user('address_a');self.b=U.objects.create_user('address_b');self.admin=U.objects.create_superuser('address_root')
        team=Department.objects.create(name='주소 검증팀');self.e=Employee.objects.create(user=self.a,name='가상 A',department=team);self.other=Employee.objects.create(user=self.b,name='가상 B',department=team)
        self.info=StaffEmployment.objects.create(employee=self.e,joined_on=date(2020,1,15),staff_number='T-100')
        self.client.force_login(self.a)
    def payload(self,**changes):
        d={'action':'address','postal_code':'12345','address':'가상의 테스트 도로 1','detail':'검증용 상세','revision':'0'};d.update(changes);return d
    def test_full_month_tenure_boundaries_and_leap_day(self):
        self.assertEqual(tenure_text(self.info,date(2026,11,14)),'6년 9개월')
        self.assertEqual(tenure_text(self.info,date(2026,11,15)),'6년 10개월')
        self.assertEqual(tenure_text(self.info,date(2020,1,20)),'1개월 미만')
        self.assertEqual(tenure_text(self.info,date(2019,1,1)),'입사 예정')
        self.info.ended_on=date(2021,3,15);self.assertEqual(tenure_text(self.info,date(2026,1,1)),'1년 2개월')
        self.info.joined_on=date(2024,2,29);self.info.ended_on=None;self.assertEqual(tenure_text(self.info,date(2025,3,1)),'1년')
        self.assertEqual(tenure_text(None),'입사일 미등록')
    def test_self_can_save_address_without_changing_employment(self):
        r=self.client.post('/accounts/me/',self.payload(employee=self.other.pk,staff_number='CHANGED',job_title='대표'))
        self.assertEqual(r.status_code,302);saved=EmployeeAddress.objects.get(employee=self.e);self.assertEqual(saved.address,'가상의 테스트 도로 1');self.assertEqual(saved.revision,1)
        self.info.refresh_from_db();self.assertEqual(self.info.staff_number,'T-100');self.assertFalse(EmployeeAddress.objects.filter(employee=self.other).exists())
        r=self.client.get('/accounts/me/');self.assertContains(r,'가상의 테스트 도로 1');self.assertContains(r,'근속기간')
        self.assertEqual(set(r.context['card']),{'name','department','job_title','staff_number'});self.assertNotIn('가상의',json.dumps(r.context['card']))
    def test_foreign_address_unavailable_and_administrator_can_edit(self):
        EmployeeAddress.objects.create(employee=self.other,address='비공개 주소 검증')
        self.assertNotContains(self.client.get('/accounts/me/?employee_id='+str(self.other.pk)),'비공개 주소 검증')
        self.assertEqual(self.client.post(f'/accounts/employees/{self.other.pk}/profile/',self.payload()).status_code,403)
        self.client.force_login(self.admin);self.assertContains(self.client.get(f'/accounts/employees/{self.other.pk}/profile/'),'비공개 주소 검증')
        self.assertEqual(self.client.post(f'/accounts/employees/{self.other.pk}/profile/',self.payload()).status_code,302)
    def test_address_is_optional_and_can_be_cleared(self):
        self.client.post('/accounts/me/',self.payload())
        r=self.client.post('/accounts/me/',self.payload(postal_code='',address='',detail='',revision=1));self.assertEqual(r.status_code,302)
        obj=EmployeeAddress.objects.get(employee=self.e);self.assertEqual((obj.postal_code,obj.address,obj.detail),('','',''))
    def test_stale_revision_preserves_latest_address(self):
        self.client.post('/accounts/me/',self.payload())
        r=self.client.post('/accounts/me/',self.payload(address='오래된 화면의 입력'))
        self.assertContains(r,'다른 화면에서 주소가 변경되었습니다.');self.assertEqual(EmployeeAddress.objects.get(employee=self.e).address,'가상의 테스트 도로 1')
    def test_validation_retains_entered_values_without_saving(self):
        for changes in [{'postal_code':'12a45'},{'address':''},{'revision':'-1'},{'revision':''}]:
            r=self.client.post('/accounts/me/',self.payload(**changes));self.assertEqual(r.status_code,200);self.assertTrue(r.context['address_form'].errors)
        self.assertEqual(EmployeeAddress.objects.count(),0)
        self.assertTrue(AddressForm({'postal_code':'','address':'','detail':'','revision':0}).is_valid())
    def test_no_join_date_is_invented_when_address_saved(self):
        self.client.force_login(self.b);self.assertEqual(self.client.post('/accounts/me/',self.payload()).status_code,302)
        self.assertFalse(StaffEmployment.objects.filter(employee=self.other).exists())
    def test_audit_contains_changed_field_names_not_private_address(self):
        self.client.post('/accounts/me/',self.payload());audit=SystemAudit.objects.get(target_type='EmployeeAddress')
        data=json.dumps([audit.before,audit.after],ensure_ascii=False)
        self.assertNotIn('12345',data);self.assertNotIn('테스트 도로',data);self.assertNotIn('검증용 상세',data);self.assertIn('기본 주소',data)
    def test_csrf_and_technical_account_without_staff(self):
        c=Client(enforce_csrf_checks=True);c.force_login(self.a);self.assertEqual(c.post('/accounts/me/',self.payload()).status_code,403)
        self.client.force_login(self.admin);self.assertEqual(self.client.post('/accounts/me/',self.payload()).status_code,403)
