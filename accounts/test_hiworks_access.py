from datetime import timedelta
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.test import TestCase,override_settings
from django.utils import timezone
from django.core.exceptions import ValidationError
from tasks.models import Department,Employee,HiworksLink,HiworksDirectoryCache
from .models import AccessProfile
from .hiworks_access import allowed,refresh_directory,require_mailbox
from .lifecycle import account_allowed
from django_otp.plugins.otp_totp.models import TOTPDevice
from django.contrib.sessions.models import Session

@override_settings(WORKHUB_REQUIRE_HIWORKS=True,PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class StaffMembershipTests(TestCase):
    def setUp(self):
        U=get_user_model()
        self.admin=U.objects.create_superuser('owner',password='test-password')
        self.user=U.objects.create_user('worker@gunsystems.co.kr',email='worker@gunsystems.co.kr',password='test-password')
        self.employee=Employee.objects.create(name='직원',user=self.user,department=Department.objects.create(name='인증 테스트'))
        self.access=AccessProfile.objects.create(user=self.user,email=self.user.email,verified_at=timezone.now(),require_mfa=False)
        self.row={'email':self.user.email,'user_id':'worker','remote_id':'42','name':'직원','department':''}
        self.cache=HiworksDirectoryCache.objects.create(checked_at=timezone.now(),rows=[self.row])
        self.link=HiworksLink.objects.create(employee=self.employee,email=self.user.email,user_id='worker',remote_id='42')
        self.device=TOTPDevice.objects.create(user=self.user,confirmed=True)

    def test_only_verified_bound_current_company_member_allowed(self):
        self.assertTrue(allowed(self.user))
        self.link.notifications_enabled=False;self.link.save()
        self.assertTrue(allowed(self.user))
        self.access.verified_at=None;self.access.save();self.user.refresh_from_db()
        self.assertFalse(allowed(self.user))

    def test_missing_stale_or_changed_remote_identity_fails_closed(self):
        self.cache.checked_at=timezone.now()-timedelta(minutes=6);self.cache.save()
        self.assertFalse(allowed(self.user))
        self.cache.checked_at=timezone.now();self.cache.rows=[];self.cache.save()
        self.assertFalse(allowed(self.user))
        self.cache.rows=[{**self.row,'remote_id':'reissued'}];self.cache.save()
        self.assertFalse(allowed(self.user))
        self.assertTrue(allowed(self.admin))

    def test_same_domain_or_admin_flag_does_not_bypass_check(self):
        self.cache.rows=[];self.cache.save()
        self.user.is_staff=True;self.user.save()
        self.assertFalse(account_allowed(self.user))
        self.client.force_login(self.user)
        for path in ['/','/dashboard/','/work/','/work/personal/','/accounts/employees/','/accounts/administrators/','/work/hiworks/','/admin/tasks/employee/']:
            response=self.client.get(path)
            self.assertEqual(response.status_code,403,path)
            self.assertContains(response,'회사 직원 등록 확인',status_code=403)
            self.assertEqual(response['Cache-Control'],'no-store')
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get('/accounts/employees/').status_code,200)

    def test_existing_session_loses_access_when_member_removed(self):
        self.client.force_login(self.user)
        session=self.client.session;session['otp_device_id']=self.device.persistent_id;session.save()
        self.assertEqual(self.client.get('/dashboard/').status_code,200)
        self.cache.rows=[];self.cache.save()
        self.assertEqual(self.client.get('/dashboard/').status_code,403)

    def test_unregistered_email_cannot_be_invited_or_login(self):
        with self.assertRaises(ValidationError): require_mailbox('outsider@gunsystems.co.kr')
        self.cache.rows=[];self.cache.save()
        response=self.client.post('/login/',{'username':self.user.email,'password':'test-password'})
        self.assertEqual(response.status_code,200)
        self.assertNotIn('_auth_user_id',self.client.session)

    @patch('tasks.hiworks.directory_rows')
    def test_api_failure_never_extends_last_success(self, rows):
        self.cache.checked_at=timezone.now()-timedelta(minutes=6);self.cache.save()
        rows.side_effect=ValidationError('허용 IP 확인 필요')
        self.assertFalse(refresh_directory(force=True))
        self.cache.refresh_from_db()
        self.assertLess(self.cache.checked_at,timezone.now()-timedelta(minutes=5))
        self.assertFalse(allowed(self.user))
        self.assertEqual(self.cache.error,'허용 IP 확인 필요')

    @patch('tasks.hiworks.directory_rows')
    def test_refresh_updates_and_removes_members_authoritatively(self, rows):
        rows.return_value=[]
        self.client.force_login(self.user)
        key=self.client.session.session_key
        self.assertTrue(Session.objects.filter(session_key=key).exists())
        self.assertTrue(refresh_directory(force=True))
        self.assertFalse(Session.objects.filter(session_key=key).exists())
        self.assertFalse(allowed(self.user))

    def test_native_account_routes_also_require_current_otp_session(self):
        self.user.is_staff=True;self.user.save()
        self.client.force_login(self.user)
        response=self.client.get('/accounts/employees/')
        self.assertRedirects(response,'/accounts/mfa/verify/',fetch_redirect_response=False)
        self.device.delete()
        response=self.client.get('/accounts/employees/')
        self.assertRedirects(response,'/accounts/mfa/setup/',fetch_redirect_response=False)

    @patch('accounts.services.deliver')
    def test_unknown_company_mailbox_never_receives_invitation(self, deliver):
        from .services import invite
        from .models import Invitation
        employee=Employee.objects.create(name='미등록',department=self.employee.department)
        with self.assertRaises(ValidationError): invite(employee,'outside@gunsystems.co.kr')
        deliver.assert_not_called()
        self.assertFalse(Invitation.objects.filter(employee=employee).exists())

    @patch('accounts.services.deliver')
    def test_existing_invitation_cannot_bypass_current_membership(self, deliver):
        from .models import Invitation
        from .services import digest
        employee=Employee.objects.create(name='미등록',department=self.employee.department)
        token='known-test-invitation'
        Invitation.objects.create(employee=employee,email='outside@gunsystems.co.kr',token_digest=digest(token),expires_at=timezone.now()+timedelta(days=1))
        before=get_user_model().objects.count()
        response=self.client.post('/accounts/activate/'+token+'/',{'action':'send'})
        self.assertContains(response,'하이웍스에 등록된 회사 이메일만')
        self.assertEqual(get_user_model().objects.count(),before)
        deliver.assert_not_called()
