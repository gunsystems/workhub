from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone
from django_otp.plugins.otp_totp.models import TOTPDevice

from tasks.models import Employee, Department
from tasks.permissions import is_company_admin
from .models import AccessProfile
from .lifecycle import change_access, system_admin


class AdministratorTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.owner = User.objects.create_superuser('owner', password='test')
        self.member = User.objects.create_user('member', email='member@gunsystems.co.kr', password='test')
        self.department = Department.objects.create(name='관리팀')
        self.employee = Employee.objects.create(name='직원', department=self.department, user=self.member)
        self.client.force_login(self.owner)

    def test_manual_user_registration_removed_and_staff_menu_filtered(self):
        self.assertEqual(self.client.get('/admin/otp_totp/totpdevice/add/').status_code, 404)
        self.assertEqual(self.client.get('/admin/auth/user/add/').status_code, 404)
        response = self.client.get('/admin/accounts/administratoraccount/')
        self.assertContains(response, '관리자 계정 관리')
        self.assertNotContains(response, 'member@gunsystems.co.kr')
        self.assertEqual(self.client.get('/admin/accounts/administratoraccount/add/').status_code, 403)
        response = self.client.get('/admin/tasks/employee/add/')
        self.assertNotContains(response, 'name="user"')

    def test_admin_designation_requires_email_and_otp_and_does_not_grant_business_role(self):
        data = {'employee':self.employee.pk, 'action':'grant_admin', 'reason':'직원 계정 관리 담당'}
        self.assertContains(self.client.post('/accounts/administrators/', data), '먼저 완료')
        AccessProfile.objects.create(user=self.member, email=self.member.email, verified_at=timezone.now())
        self.assertContains(self.client.post('/accounts/administrators/', data), '먼저 완료')
        TOTPDevice.objects.create(user=self.member, confirmed=True)
        self.assertEqual(self.client.post('/accounts/administrators/', data).status_code, 302)
        self.member.refresh_from_db()
        self.assertTrue(system_admin(self.member))
        self.assertFalse(is_company_admin(self.member))
        self.assertFalse(self.member.is_superuser)
        self.employee.refresh_from_db()
        self.assertEqual(self.employee.role, 'employee')
        data['action'] = 'revoke_admin'
        self.client.post('/accounts/administrators/', data)
        self.member.refresh_from_db()
        self.assertFalse(self.member.is_staff)

    def test_subadmin_cannot_manage_owner_or_other_admin(self):
        AccessProfile.objects.create(user=self.member, email=self.member.email, verified_at=timezone.now())
        TOTPDevice.objects.create(user=self.member, confirmed=True)
        self.member.is_staff = True
        self.member.save()
        self.client.force_login(self.member)
        self.assertRedirects(self.client.get('/accounts/administrators/'),'/accounts/mfa/verify/',fetch_redirect_response=False)
        self.assertEqual(self.client.get('/admin/').status_code, 302)  # OTP still required.
        session=self.client.session
        session['otp_device_id']=TOTPDevice.objects.get(user=self.member,confirmed=True).persistent_id
        session.save()
        self.assertEqual(self.client.get('/accounts/administrators/').status_code,403)
        with self.assertRaises(ValidationError):
            change_access(self.member, self.employee.pk, 'suspend', '본인 중지 시도')
        another = get_user_model().objects.create_user('other-admin', is_staff=True)
        target = Employee.objects.create(name='다른 관리자', department=self.department, user=another)
        with self.assertRaises(ValidationError):
            change_access(self.member, target.pk, 'reset_mfa', '다른 관리자 변경 시도')
