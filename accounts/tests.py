import re
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import check_password
from django.core import mail
from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.utils import timezone
from django_otp.oath import totp
from django_otp.plugins.otp_totp.models import TOTPDevice
from tasks.models import Department, Employee, Task
from .models import Invitation, AccessProfile, RecoveryCode, RateBucket
from .services import invite, digest


@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
                   PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class AccountFlowTests(TestCase):
    def setUp(self):
        self.department = Department.objects.create(name='인증테스트팀')
        self.employee = Employee.objects.create(name='테스트 직원', department=self.department)
        self.email = 'employee@gunsystems.co.kr'

    def invitation(self):
        invite(self.employee, self.email)
        return re.search(r'https://[^\s]+(/accounts/activate/[^\s]+)', mail.outbox[-1].body).group(1)

    def activate(self):
        url = self.invitation()
        self.client.get(url)
        self.client.post(url, {'action':'send'})
        code = re.search(r'인증 코드: (\d{6})', mail.outbox[-1].body).group(1)
        self.client.post(url, {'code':code})
        response = self.client.post(url, {'new_password1':'Test-password-873!', 'new_password2':'Test-password-873!'})
        self.assertRedirects(response, '/accounts/mfa/setup/', fetch_redirect_response=False)
        self.employee.refresh_from_db()
        return url, self.employee.user

    def enroll(self):
        self.client.get('/accounts/mfa/setup/')
        device = TOTPDevice.objects.get(user=self.employee.user)
        token = str(totp(device.bin_key, step=device.step, t0=device.t0, digits=device.digits)).zfill(6)
        response = self.client.post('/accounts/mfa/setup/', {'code':token})
        self.assertContains(response, 'OTP 등록 완료')
        return device, response.context['codes']

    def test_full_invite_email_password_mfa_and_employee_workflow(self):
        url, user = self.activate()
        self.assertTrue(user.check_password('Test-password-873!'))
        self.assertTrue(user.workhub_access.verified_at)
        self.assertRedirects(self.client.get('/dashboard/'), '/accounts/mfa/setup/', fetch_redirect_response=False)
        device, codes = self.enroll()
        self.assertEqual(len(codes), 8)
        self.assertEqual(self.client.get('/dashboard/').status_code, 200)
        self.assertContains(self.client.get(url), '사용할 수 없는 초대')
        self.client.post('/logout/')
        response = self.client.post('/login/', {'username':self.email, 'password':'Test-password-873!'})
        self.assertRedirects(response, '/accounts/mfa/verify/', fetch_redirect_response=False)
        self.assertNotIn('_auth_user_id', self.client.session)
        self.assertEqual(self.client.get('/dashboard/').status_code, 302)
        # A recovery code provides one successful second factor only.
        self.client.post('/accounts/mfa/verify/', {'code':codes[0]})
        self.assertEqual(RecoveryCode.objects.filter(user=user).count(), 7)
        self.assertEqual(self.client.get('/dashboard/').status_code, 200)
        self.assertEqual(self.client.get('/admin/').status_code, 302)
        self.client.post('/logout/')
        self.client.post('/login/', {'username':self.email, 'password':'Test-password-873!'})
        self.assertContains(self.client.post('/accounts/mfa/verify/', {'code':codes[0]}), '올바르지 않습니다')

    def test_invitation_domain_expiry_resend_and_link_not_sufficient(self):
        for value in ['x@other.co.kr', 'x@gunsystems.co.kr.evil.com', 'x@sub.gunsystems.co.kr']:
            with self.assertRaises(ValidationError):
                invite(self.employee, value)
        old = self.invitation()
        self.client.get(old)
        response = self.client.post(old, {'new_password1':'Test-password-873!', 'new_password2':'Test-password-873!'})
        self.assertIsNone(Employee.objects.get(pk=self.employee.pk).user_id)
        new = self.invitation()
        self.assertNotEqual(old, new)
        self.assertContains(self.client.get(old), '사용할 수 없는 초대')
        Invitation.objects.update(expires_at=timezone.now()-timedelta(seconds=1))
        self.assertContains(self.client.get(new), '사용할 수 없는 초대')

    def test_code_is_hashed_attempt_limited_and_verification_session_bound(self):
        url = self.invitation()
        self.client.get(url)
        self.client.post(url, {'action':'send'})
        code = re.search(r'인증 코드: (\d{6})', mail.outbox[-1].body).group(1)
        obj = Invitation.objects.get(employee=self.employee)
        self.assertNotEqual(obj.code_digest, code)
        self.assertTrue(check_password(code, obj.code_digest))
        self.client.post(url, {'code':code})
        other = self.client_class()
        self.assertContains(other.get(url), '회사 이메일 인증')
        other.post(url, {'new_password1':'Test-password-873!', 'new_password2':'Test-password-873!'})
        self.assertIsNone(Employee.objects.get(pk=self.employee.pk).user_id)
        # Expiration is checked even after verification.
        Invitation.objects.update(expires_at=timezone.now()-timedelta(seconds=1))
        self.assertContains(self.client.post(url, {'new_password1':'Test-password-873!', 'new_password2':'Test-password-873!'}), '사용할 수 없는 초대')

    def test_wrong_code_limits_and_resend_cooldown(self):
        url = self.invitation()
        self.client.get(url)
        self.client.post(url, {'action':'send'})
        code = re.search(r'인증 코드: (\d{6})', mail.outbox[-1].body).group(1)
        for _ in range(5):
            self.client.post(url, {'code':'invalid'})
        self.assertContains(self.client.post(url, {'code':code}), '올바르지 않거나 만료')
        self.assertContains(self.client.post(url, {'action':'send'}), '60초')
        self.assertEqual(len(mail.outbox), 2)

    def test_delivery_failure_retains_employee_and_records_failure(self):
        with patch('accounts.services.send_mail', side_effect=OSError('smtp failed')):
            with self.assertRaises(ValidationError):
                invite(self.employee, self.email)
        self.assertTrue(Employee.objects.filter(pk=self.employee.pk).exists())
        self.assertEqual(Invitation.objects.get(employee=self.employee).delivery, 'failed')
        self.assertFalse(get_user_model().objects.filter(email=self.email).exists())

    def test_disabled_employee_and_pending_mfa_expiry(self):
        _, user = self.activate()
        self.enroll()
        self.client.post('/logout/')
        self.client.post('/login/', {'username':self.email, 'password':'Test-password-873!'})
        session = self.client.session
        session['pending_mfa_at'] = (timezone.now()-timedelta(minutes=6)).timestamp()
        session.save()
        self.assertRedirects(self.client.get('/accounts/mfa/verify/'), '/login/', fetch_redirect_response=False)
        user.is_active = False
        user.save()
        self.client.post('/login/', {'username':self.email, 'password':'Test-password-873!'})
        self.assertNotIn('_auth_user_id', self.client.session)

    def test_admin_login_cannot_bypass_enrolled_mfa(self):
        user = get_user_model().objects.create_superuser('admin', password='test')
        TOTPDevice.objects.create(user=user, confirmed=True)
        self.client.post('/admin/login/', {'username':'admin', 'password':'test', 'next':'/admin/'})
        self.assertNotIn('_auth_user_id', self.client.session)
        self.assertEqual(self.client.get('/admin/').status_code, 302)
        self.assertContains(self.client.get('/admin/', follow=True), '2차 인증')
        # Even a session established by another password-only entry point is gated.
        self.client.force_login(user)
        self.assertRedirects(self.client.get('/admin/'), '/accounts/mfa/verify/', fetch_redirect_response=False)
        self.assertRedirects(self.client.get('/'), '/accounts/mfa/verify/', fetch_redirect_response=False)

    def test_legacy_admin_preserved_and_new_employee_admin_invites(self):
        user = get_user_model().objects.create_superuser('admin', password='test')
        self.client.force_login(user)
        self.assertEqual(self.client.get('/admin/').status_code, 200)
        response = self.client.post('/admin/tasks/employee/add/', {
            'name':'초대직원', 'department':self.department.pk, 'role':'employee',
            'invitation_email':'second@gunsystems.co.kr', '_save':'저장'})
        self.assertEqual(response.status_code, 302)
        self.assertTrue(Invitation.objects.filter(email='second@gunsystems.co.kr').exists())
        self.assertEqual(len(mail.outbox), 1)

    def test_otp_replay_is_rejected_and_login_throttled(self):
        _, user = self.activate()
        device, _ = self.enroll()
        device.refresh_from_db()
        token = str(totp(device.bin_key, step=device.step, t0=device.t0, digits=device.digits)).zfill(6)
        self.client.post('/logout/')
        self.client.post('/login/', {'username':self.email, 'password':'Test-password-873!'})
        self.assertContains(self.client.post('/accounts/mfa/verify/', {'code':token}), '올바르지 않습니다')
        for _ in range(10):
            self.client.post('/login/', {'username':self.email, 'password':'wrong'})
        response = self.client.post('/login/', {'username':self.email, 'password':'Test-password-873!'})
        self.assertContains(response, '로그인할 수 없습니다')

    def test_invited_employee_reports_and_admin_returns_then_approves(self):
        _, user = self.activate()
        self.enroll()
        admin = get_user_model().objects.create_superuser('owner', password='admin-test')
        owner_client = self.client_class()
        owner_client.post('/login/', {'username':'owner','password':'admin-test'})
        today = timezone.localdate()
        response = owner_client.post('/tasks/new/', {'title':'인증 후 상호 업무 테스트', 'assignee':self.employee.pk,
            'status':'todo', 'importance':'normal', 'priority':3, 'start_date':today, 'due_date':today,
            'completion_criteria':'검토 가능한 결과물', 'report_days':3, 'review_days':2})
        self.assertEqual(response.status_code, 302)
        task = Task.objects.get(title='인증 후 상호 업무 테스트')
        self.assertEqual(self.client.post(f'/tasks/{task.pk}/accept/').status_code, 302)
        self.client.post(f'/tasks/{task.pk}/actions/progress/', {'progress':50, 'body':'작업 중', 'delay_reason':''})
        self.client.post(f'/tasks/{task.pk}/actions/submit/', {'body':'1차 결과물'})
        self.assertEqual(self.client.post(f'/tasks/{task.pk}/actions/review/', {'decision':'approve','quality_score':100,'body':'스스로 승인'}).status_code, 403)
        owner_client.post(f'/tasks/{task.pk}/actions/review/', {'decision':'return','body':'결과 보완 필요'})
        self.assertContains(self.client.get('/dashboard/?scope=mine'), '결과 보완 필요')
        self.client.post(f'/tasks/{task.pk}/actions/submit/', {'body':'보완한 최종 결과물'})
        owner_client.post(f'/tasks/{task.pk}/actions/review/', {'decision':'approve','quality_score':90,'body':'최종 검토 완료'})
        task.refresh_from_db()
        self.assertEqual(task.status, 'done')
        self.assertEqual(task.quality_score, 90)
        self.assertEqual(task.events.filter(kind='submitted').count(), 2)

    def test_password_reset_does_not_remove_mfa_and_uses_configured_origin(self):
        _, user = self.activate()
        self.enroll()
        self.client.post('/logout/')
        self.client.post('/accounts/password/reset/', {'email':self.email})
        reset_body = mail.outbox[-1].body
        self.assertIn('https://gunsystems-workhub-test.onrender.com/accounts/password/reset/', reset_body)
        url = re.search(r'https://[^\s]+(/accounts/password/reset/[^\s]+)', reset_body).group(1)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 302)
        url = response.url
        self.client.post(url, {'new_password1':'Changed-password-983!', 'new_password2':'Changed-password-983!'})
        user.refresh_from_db()
        self.assertTrue(user.check_password('Changed-password-983!'))
        self.assertTrue(TOTPDevice.objects.filter(user=user, confirmed=True).exists())
        response = self.client.post('/login/', {'username':self.email,'password':'Changed-password-983!'})
        self.assertRedirects(response, '/accounts/mfa/verify/', fetch_redirect_response=False)

    def test_recovery_regeneration_and_phone_replacement_require_reauthentication(self):
        _, user = self.activate()
        _, codes = self.enroll()
        response = self.client.post('/accounts/mfa/recovery/', {'action':'regenerate','password':'bad','code':codes[0]})
        self.assertContains(response, '비밀번호와 OTP/복구 코드')
        self.assertEqual(RecoveryCode.objects.filter(user=user).count(), 8)
        response = self.client.post('/accounts/mfa/recovery/', {'action':'regenerate','password':'Test-password-873!','code':codes[0]})
        self.assertEqual(len(response.context['codes']), 8)
        fresh = response.context['codes']
        self.assertFalse(any(check_password(codes[1], obj.digest) for obj in RecoveryCode.objects.filter(user=user)))
        response = self.client.post('/accounts/mfa/recovery/', {'action':'replace','password':'Test-password-873!','code':fresh[0]})
        self.assertRedirects(response, '/accounts/mfa/setup/', fetch_redirect_response=False)
        self.assertFalse(TOTPDevice.objects.filter(user=user, confirmed=True).exists())
        self.assertRedirects(self.client.get('/dashboard/'), '/accounts/mfa/setup/', fetch_redirect_response=False)

    def test_unauthenticated_and_csrf_requests_cannot_enroll_or_activate(self):
        self.assertEqual(self.client.get('/accounts/mfa/setup/').status_code, 302)
        url = self.invitation()
        csrf_client = self.client_class(enforce_csrf_checks=True)
        self.assertEqual(csrf_client.post(url, {'action':'send'}).status_code, 403)
        self.assertEqual(len(mail.outbox), 1)
