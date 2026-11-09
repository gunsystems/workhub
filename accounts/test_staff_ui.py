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
        directory=patch('tasks.hiworks.directory_rows',return_value=[{'remote_id':'100','user_id':'new','name':'신규 직원','email':'new@gunsystems.co.kr','department':self.department.name}])
        directory.start();self.addCleanup(directory.stop)

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
        response=self.client.post('/accounts/employees/new/',{'hiworks_employee':'100','name':'위조 이름','department':self.department.pk,'position':'member','role':'employee','invitation_email':'forged@gunsystems.co.kr'})
        self.assertEqual(response.status_code,302)
        employee=Employee.objects.get(name='신규 직원')
        self.assertIsNone(employee.user_id)
        invite.assert_called_once_with(employee,'new@gunsystems.co.kr')

    @patch('accounts.staff_views.invite')
    def test_registered_or_unknown_employee_cannot_be_invited(self,invite):
        self.member.email='new@gunsystems.co.kr';self.member.save()
        for identity in ['100','unknown','']:
            response=self.client.post('/accounts/employees/new/',{'hiworks_employee':identity,'name':'신규 직원','department':self.department.pk,'position':'member','role':'employee','invitation_email':'new@gunsystems.co.kr'})
            self.assertEqual(response.status_code,200)
        self.assertFalse(Employee.objects.filter(name='신규 직원').exists())
        invite.assert_not_called()

    @patch('accounts.staff_views.invite')
    def test_directory_failure_blocks_invitation_and_get_does_not_send(self,invite):
        from django.core.exceptions import ValidationError
        response=self.client.get('/accounts/employees/new/')
        self.assertContains(response,'직원 검색')
        self.assertContains(response,'신규 직원')
        self.assertEqual(response['Cache-Control'],'no-store')
        with patch('tasks.hiworks.directory_rows',side_effect=ValidationError('연결 확인 필요')):
            response=self.client.post('/accounts/employees/new/',{'hiworks_employee':'100','name':'신규 직원','department':self.department.pk,'position':'member','role':'employee','invitation_email':'new@gunsystems.co.kr'})
        self.assertContains(response,'연결 확인 필요')
        self.assertFalse(Employee.objects.filter(name='신규 직원').exists())
        invite.assert_not_called()

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

    def test_supervisor_choices_exclude_examples_pending_inactive_and_self(self):
        from .staff_views import StaffForm
        from .models import AccessProfile,Invitation
        from django.utils import timezone
        from django_otp.plugins.otp_totp.models import TOTPDevice
        examples=Employee.objects.create(name='예시 직원',department=self.department)
        candidates=[]
        for index in range(4):
            email=f'real{index}@gunsystems.co.kr'
            user=get_user_model().objects.create_user(f'real{index}',email=email)
            employee=Employee.objects.create(user=user,name=f'실제 직원{index}',department=self.department,position='leader' if index!=3 else 'member')
            AccessProfile.objects.create(user=user,email=email,verified_at=timezone.now())
            Invitation.objects.create(employee=employee,email=email,token_digest=str(index),expires_at=timezone.now(),used_at=timezone.now())
            TOTPDevice.objects.create(user=user,confirmed=index!=1)
            candidates.append(employee)
        candidates[2].is_active=False;candidates[2].save()
        form=StaffForm(instance=self.employee,user=self.admin)
        self.assertEqual(list(form.fields['supervisor'].queryset.values_list('pk',flat=True)),[candidates[0].pk])
        self.assertFalse(StaffForm(instance=candidates[0],user=self.admin).fields['supervisor'].queryset.exists())
        for position in ['head','executive']:
            candidates[0].position=position;candidates[0].save()
            self.assertEqual(list(StaffForm(instance=self.employee,user=self.admin).fields['supervisor'].queryset.values_list('pk',flat=True)),[candidates[0].pk])
        forged=StaffForm(data={'name':'직원','department':self.department.pk,'position':'member','role':'employee','supervisor':examples.pk},instance=self.employee,user=self.admin)
        self.assertFalse(forged.is_valid())
        self.assertIn('supervisor',forged.errors)
        forged_member=StaffForm(data={'name':'직원','department':self.department.pk,'position':'member','role':'employee','supervisor':candidates[3].pk},instance=self.employee,user=self.admin)
        self.assertFalse(forged_member.is_valid())
        self.assertIn('supervisor',forged_member.errors)
