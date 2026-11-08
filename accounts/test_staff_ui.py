from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.test import TestCase,override_settings
from tasks.models import Department,Employee

@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class StaffUITests(TestCase):
    def setUp(self):
        U=get_user_model()
        self.admin=U.objects.create_superuser('owner',password='test-password')
        self.member=U.objects.create_user('member',password='test-password')
        self.department=Department.objects.create(name='직원화면 테스트')
        self.employee=Employee.objects.create(user=self.member,name='테스트 직원',department=self.department)
        self.client.force_login(self.admin)

    def test_native_list_and_forms_keep_workhub_shell(self):
        for url in ['/accounts/employees/','/accounts/employees/new/',f'/accounts/employees/{self.employee.pk}/edit/','/accounts/departments/']:
            response=self.client.get(url)
            self.assertEqual(response.status_code,200)
            self.assertContains(response,'app-sidebar')
            self.assertContains(response,'관리자 전용')
        response=self.client.get('/accounts/employees/')
        self.assertContains(response,'테스트 직원')
        self.assertNotContains(response,'/admin/tasks/employee/')

    @patch('accounts.staff_views.invite')
    def test_add_employee_invites_without_creating_login(self, invite):
        response=self.client.post('/accounts/employees/new/',{'name':'신규 직원','department':self.department.pk,'position':'member','role':'employee','invitation_email':'new@gunsystems.co.kr'})
        self.assertEqual(response.status_code,302)
        employee=Employee.objects.get(name='신규 직원')
        self.assertIsNone(employee.user_id)
        invite.assert_called_once_with(employee,'new@gunsystems.co.kr')

    def test_regular_employee_cannot_use_admin_operations_or_forge_post(self):
        self.client.force_login(self.member)
        for url in ['/accounts/employees/','/accounts/employees/new/',f'/accounts/employees/{self.employee.pk}/edit/','/accounts/departments/']:
            self.assertEqual(self.client.get(url).status_code,403)
            self.assertEqual(self.client.post(url,{'name':'forged'}).status_code,403)
        self.assertNotContains(self.client.get('/dashboard/'),'관리자 전용')

    def test_protected_owner_record_cannot_be_edited(self):
        owner=Employee.objects.create(user=self.admin,name='관리자',department=self.department)
        self.assertEqual(self.client.get(f'/accounts/employees/{owner.pk}/edit/').status_code,403)
        self.assertEqual(self.client.post(f'/accounts/employees/{owner.pk}/edit/',{'name':'변경'}).status_code,403)
        owner.refresh_from_db();self.assertEqual(owner.name,'관리자')

    def test_subadministrator_form_cannot_escalate_business_role_or_position(self):
        from .staff_views import StaffForm
        form=StaffForm(data={'name':'직원','department':self.department.pk,'position':'executive','role':'manager'},instance=self.employee,user=self.member)
        self.assertTrue(form.is_valid(),form.errors)
        self.assertEqual(form.cleaned_data['position'],'member')
        self.assertEqual(form.cleaned_data['role'],'employee')
