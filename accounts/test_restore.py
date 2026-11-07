import re
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import make_password
from django.core import mail
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import Client
from django.utils import timezone

from .tests import AccountFlowTests
from .models import AccessProfile, RecoveryCode, OTPResetChallenge, AccountEvent
from .lifecycle import change_access
from .views import verify_factor
from django_otp.plugins.otp_totp.models import TOTPDevice


class OTPRestoreTests(AccountFlowTests):
    # AccountFlowTests' integration coverage also runs under this setup.
    def reset_account(self):
        _, self.user = self.activate()
        self.old_device, _ = self.enroll()
        self.admin = get_user_model().objects.create_superuser('reset-owner', password='owner-password')
        change_access(self.admin, self.employee.pk, 'reset_mfa', '직원과 대면하여 본인 확인')
        self.user.refresh_from_db()
        return self.user

    def password_login(self, client=None):
        return (client or self.client).post('/login/', {'username':self.email, 'password':'Test-password-873!'})

    def send_and_verify(self):
        url = '/accounts/mfa/restore/'
        self.client.get(url)
        self.client.post(url, {'action':'send'})
        code = re.search(r'인증 코드: (\d{6})', mail.outbox[-1].body).group(1)
        self.assertEqual(mail.outbox[-1].to, [self.email])
        self.assertRedirects(self.client.post(url, {'code':code}), '/accounts/mfa/setup/', fetch_redirect_response=False)
        return code

    def test_admin_reset_revokes_old_access_and_requires_password_email_new_otp(self):
        user = self.reset_account()
        self.assertNotIn('_auth_user_id', self.client.get('/accounts/mfa/restore/').wsgi_request.session)
        self.assertFalse(TOTPDevice.objects.filter(user=user).exists())
        self.assertTrue(user.workhub_access.otp_reset_key)
        self.assertTrue(user.check_password('Test-password-873!'))
        self.password_login()
        for url in ['/dashboard/', '/admin/', '/accounts/mfa/setup/', '/accounts/mfa/recovery/']:
            self.assertRedirects(self.client.get(url), '/accounts/mfa/restore/', fetch_redirect_response=False)
        self.send_and_verify()
        self.assertRedirects(self.client.get('/dashboard/'), '/accounts/mfa/restore/', fetch_redirect_response=False)
        other = Client()
        self.password_login(other)
        self.assertRedirects(other.get('/accounts/mfa/setup/'), '/accounts/mfa/restore/', fetch_redirect_response=False)
        self.enroll()
        user.refresh_from_db()
        self.assertFalse(user.workhub_access.otp_reset_key)
        self.assertFalse(OTPResetChallenge.objects.filter(user=user).exists())
        self.assertEqual(self.client.get('/dashboard/').status_code, 200)
        self.assertNotIn('_auth_user_id', other.get('/dashboard/').wsgi_request.session)
        self.assertEqual(AccountEvent.objects.filter(action='reset_mfa').count(), 1)

    def test_restore_email_bound_to_session_and_limited(self):
        self.reset_account()
        self.password_login()
        self.client.get('/accounts/mfa/restore/')
        self.client.post('/accounts/mfa/restore/', {'action':'send'})
        code = re.search(r'인증 코드: (\d{6})', mail.outbox[-1].body).group(1)
        other = Client()
        self.password_login(other)
        other.get('/accounts/mfa/restore/')
        self.assertContains(other.post('/accounts/mfa/restore/', {'code':code}), '올바르지 않거나 만료')
        self.assertContains(self.client.post('/accounts/mfa/restore/', {'action':'send'}), '60초')
        for _ in range(4):
            self.client.post('/accounts/mfa/restore/', {'code':'invalid'})
        self.assertContains(self.client.post('/accounts/mfa/restore/', {'code':code}), '올바르지 않거나 만료')

    def test_repeated_reset_invalidates_verified_proof_and_expiration(self):
        self.reset_account()
        self.password_login()
        self.send_and_verify()
        session = self.client.session
        session['otp_restore_proof']['at'] = (timezone.now()-timedelta(minutes=11)).timestamp()
        session.save()
        self.assertRedirects(self.client.get('/accounts/mfa/setup/'), '/accounts/mfa/restore/', fetch_redirect_response=False)
        change_access(self.admin, self.employee.pk, 'reset_mfa', '재확인')
        self.assertNotIn('_auth_user_id', self.client.get('/dashboard/').wsgi_request.session)

    def test_employee_cannot_reset_or_use_old_backup_codes(self):
        _, user = self.activate()
        self.enroll()
        with self.assertRaises(PermissionDenied):
            change_access(user, self.employee.pk, 'reset_mfa', '자체 초기화')
        RecoveryCode.objects.create(user=user, digest=make_password('retired-code'))
        self.assertIsNone(verify_factor(user, 'retired-code'))
        self.assertNotContains(self.client.get('/accounts/mfa/recovery/'), '복구 코드')

    def test_unverified_or_inactive_account_cannot_be_reset(self):
        self.reset_account()
        AccessProfile.objects.filter(user=self.user).update(verified_at=None)
        with self.assertRaises(ValidationError):
            change_access(self.admin, self.employee.pk, 'reset_mfa', '확인')
