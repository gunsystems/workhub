from time import time
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.test import TestCase,Client,override_settings
from django.utils import timezone
from django_otp.oath import totp
from django_otp.plugins.otp_totp.models import TOTPDevice
from django_otp import DEVICE_ID_SESSION_KEY
from tasks.models import Department,Employee
from .models import AccessProfile
from .idle import KEY,TIMEOUT

@override_settings(WORKHUB_REQUIRE_HIWORKS=False,WORKHUB_SCHEDULER_ENABLED=False,PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'],EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class AccountSwitchTests(TestCase):
    def setUp(self):
        U=get_user_model();self.admin=U.objects.create_superuser('switch-admin',password='admin-test-password')
        self.users=[];self.devices=[];team=Department.objects.create(name='계정전환 검증')
        for index in range(2):
            u=U.objects.create_user('switch'+str(index),email='switch'+str(index)+'@gunsystems.co.kr',password='employee-test-password')
            Employee.objects.create(user=u,name='전환 직원 '+str(index),department=team)
            AccessProfile.objects.create(user=u,email=u.email,verified_at=timezone.now())
            self.users.append(u);self.devices.append(TOTPDevice.objects.create(user=u,confirmed=True,name='Existing'))
    def login_as(self,user):return self.client.post('/login/',{'username':user.email or user.username,'password':'admin-test-password' if user==self.admin else 'employee-test-password'})
    def test_admin_to_enrolled_employee_does_not_show_new_qr(self):
        self.client.force_login(self.admin);self.assertEqual(self.login_as(self.users[0]).url,'/accounts/mfa/verify/')
        self.assertNotIn('_auth_user_id',self.client.session)
        self.assertEqual(self.client.session['pending_mfa_user'],self.users[0].pk)
        page=self.client.get('/accounts/mfa/verify/',follow=True)
        self.assertContains(page,'2차 인증');self.assertNotContains(page,'등록 QR코드')
        self.assertFalse(TOTPDevice.objects.filter(user=self.admin).exists())
    def test_old_employee_factor_and_session_do_not_carry_over(self):
        self.client.force_login(self.users[0]);session=self.client.session;session[DEVICE_ID_SESSION_KEY]=self.devices[0].persistent_id;session['activation_nonce']='old';session.save()
        self.login_as(self.users[1]);self.assertNotIn('_auth_user_id',self.client.session);self.assertNotIn(DEVICE_ID_SESSION_KEY,self.client.session);self.assertNotIn('activation_nonce',self.client.session)
        self.assertEqual(self.client.session['pending_mfa_user'],self.users[1].pk)
        self.assertEqual(self.client.get('/work/').status_code,302)
        with patch('accounts.views.verify_factor',return_value=None) as check:self.client.post('/accounts/mfa/verify/',{'code':'123456'});self.assertEqual(check.call_args.args[0].pk,self.users[1].pk)
    def test_timeout_then_other_account_uses_existing_enrollment(self):
        self.client.force_login(self.admin);session=self.client.session;session[KEY]=time()-TIMEOUT-1;session.save()
        self.assertEqual(self.client.get('/accounts/session/status/').status_code,401)
        self.login_as(self.users[1]);page=self.client.get('/accounts/mfa/verify/',follow=True);self.assertContains(page,'2차 인증');self.assertNotContains(page,'등록 QR')
        self.assertEqual(TOTPDevice.objects.filter(user=self.users[1],confirmed=True).count(),1)
    def test_wrong_password_does_not_leave_previous_account_authenticated(self):
        self.client.force_login(self.admin);self.client.post('/login/',{'username':self.users[0].email,'password':'wrong'})
        self.assertNotIn('_auth_user_id',self.client.session);self.assertNotIn('pending_mfa_user',self.client.session)
    def test_successful_otp_logs_in_only_new_account_and_preserves_devices(self):
        before=list(TOTPDevice.objects.values_list('pk','user_id','key','confirmed'))
        self.client.force_login(self.admin);self.login_as(self.users[1]);device=self.devices[1]
        code=str(totp(device.bin_key,step=device.step,t0=device.t0,digits=device.digits)).zfill(6)
        self.assertEqual(self.client.post('/accounts/mfa/verify/',{'code':code}).url,'/dashboard/')
        self.assertEqual(int(self.client.session['_auth_user_id']),self.users[1].pk)
        self.assertEqual(self.client.session[DEVICE_ID_SESSION_KEY],device.persistent_id)
        self.assertNotIn('pending_mfa_user',self.client.session)
        self.assertEqual(before,list(TOTPDevice.objects.values_list('pk','user_id','key','confirmed')))
    def test_expired_pending_challenge_can_restart_login(self):
        self.login_as(self.users[0]);session=self.client.session;session['pending_mfa_at']=time()-301;session.save()
        self.assertEqual(self.client.get('/accounts/mfa/verify/').url,'/login/')
        self.assertNotIn('pending_mfa_user',self.client.session);self.assertNotIn('pending_mfa_at',self.client.session)
    def test_restart_login_is_post_only_and_clears_pending_state(self):
        self.login_as(self.users[0]);self.assertEqual(self.client.get('/accounts/mfa/switch/').status_code,405)
        self.assertEqual(self.client.post('/accounts/mfa/switch/').url,'/login/')
        self.assertNotIn('pending_mfa_user',self.client.session);self.assertContains(self.client.get('/login/'),'이메일 또는 관리자 아이디')
        self.assertEqual(Client(enforce_csrf_checks=True).post('/accounts/mfa/switch/').status_code,403)
    def test_legacy_mixed_session_fails_closed_without_enrolling_old_user(self):
        self.client.force_login(self.admin);session=self.client.session;session['pending_mfa_user']=self.users[0].pk;session['pending_mfa_at']=time();session.save()
        self.assertEqual(self.client.get('/accounts/mfa/verify/').url,'/login/')
        self.assertNotIn('_auth_user_id',self.client.session);self.assertFalse(TOTPDevice.objects.filter(user=self.admin).exists())

    def test_account_switch_remains_available_during_recovery(self):
        access=self.users[0].workhub_access;access.otp_reset_key='test-reset-required';access.save()
        self.client.force_login(self.users[0]);self.assertEqual(self.client.post('/accounts/mfa/switch/').url,'/login/');self.assertNotIn('_auth_user_id',self.client.session)
