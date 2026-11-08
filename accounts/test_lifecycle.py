from datetime import timedelta
import time
from unittest.mock import patch

import django_otp
from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import make_password
from django.contrib.auth.tokens import default_token_generator
from django.contrib.sessions.models import Session
from django.core import mail
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import Client, TestCase, override_settings
from django.utils import timezone
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode
from django_otp.plugins.otp_totp.models import TOTPDevice

from tasks.models import Department, Employee, Task, TaskEvent
from tasks.forms import TaskForm
from .models import AccessProfile, Invitation, RecoveryCode, RateBucket, AccountEvent
from .lifecycle import change_access
from .services import digest, invite


@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
                   PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class EmployeeLifecycleTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.admin = User.objects.create_superuser('owner', 'owner@gunsystems.co.kr', 'owner-password')
        self.user = User.objects.create_user('person@gunsystems.co.kr', 'person@gunsystems.co.kr', 'member-password')
        self.department = Department.objects.create(name='계정 관리 테스트')
        self.employee = Employee.objects.create(name='직원', department=self.department, user=self.user)
        AccessProfile.objects.create(user=self.user, email=self.user.email, verified_at=timezone.now())
        self.device = TOTPDevice.objects.create(user=self.user, confirmed=True)
        RecoveryCode.objects.create(user=self.user, digest=make_password('one-use-code'))
        self.invitation = Invitation.objects.create(employee=self.employee, email=self.user.email,
            token_digest=digest('old-invitation'), expires_at=timezone.now()+timedelta(days=1), used_at=timezone.now())
        self.client.force_login(self.admin)

    def employee_client(self):
        client = Client()
        client.force_login(self.user)
        session = client.session
        session[django_otp.DEVICE_ID_SESSION_KEY] = self.device.persistent_id
        session.save()
        return client

    def test_suspend_revokes_live_and_pending_sessions_and_reactivation_cannot_restore_them(self):
        client = self.employee_client()
        self.assertEqual(client.get('/dashboard/').status_code, 200)
        pending = Client()
        session = pending.session
        session['pending_mfa_user'] = self.user.pk
        session['pending_mfa_at'] = timezone.now().timestamp()
        session.save()
        pending_key = session.session_key
        change_access(self.admin, self.employee.pk, 'suspend', '퇴사 처리')
        self.user.refresh_from_db(); self.employee.refresh_from_db()
        self.assertFalse(self.user.is_active)
        self.assertFalse(self.employee.is_active)
        self.assertFalse(Session.objects.filter(session_key=pending_key).exists())
        self.assertEqual(client.get('/dashboard/').status_code, 302)
        change_access(self.admin, self.employee.pk, 'reactivate', '복직 확인')
        self.user.refresh_from_db(); self.employee.refresh_from_db()
        self.assertTrue(self.user.is_active)
        self.assertTrue(self.employee.is_active)
        self.assertTrue(self.user.check_password('member-password'))
        self.assertTrue(TOTPDevice.objects.filter(pk=self.device.pk).exists())
        self.assertEqual(client.get('/dashboard/').status_code, 302)
        self.assertEqual(pending.get('/accounts/mfa/verify/').status_code, 302)

    def test_complete_deletion_removes_credentials_invites_and_person_specific_limits(self):
        member = self.employee_client()
        user_id, employee_id = self.user.pk, self.employee.pk
        RateBucket.objects.create(key=digest(f'login-id:{self.user.email}'), count=10, expires_at=timezone.now()+timedelta(hours=1))
        RateBucket.objects.create(key=digest('login-ip:shared'), count=1, expires_at=timezone.now()+timedelta(hours=1))
        change_access(self.admin, employee_id, 'delete_employee', '계정 등록 테스트 초기화', self.user.email)
        self.assertFalse(Employee.objects.filter(pk=employee_id).exists())
        self.assertFalse(get_user_model().objects.filter(pk=user_id).exists())
        self.assertFalse(Invitation.objects.filter(pk=self.invitation.pk).exists())
        self.assertFalse(AccessProfile.objects.filter(user_id=user_id).exists())
        self.assertFalse(TOTPDevice.objects.filter(user_id=user_id).exists())
        self.assertFalse(RecoveryCode.objects.filter(user_id=user_id).exists())
        self.assertFalse(RateBucket.objects.filter(key=digest(f'login-id:{self.user.email}')).exists())
        self.assertTrue(RateBucket.objects.filter(key=digest('login-ip:shared')).exists())
        self.assertEqual(member.get('/dashboard/').status_code, 302)
        self.assertEqual(self.client.get('/admin/').status_code, 200)
        event = AccountEvent.objects.get(action='delete_employee')
        self.assertIsNone(event.employee_id)
        self.assertEqual(event.employee_reference, employee_id)
        # The same email can start from a fresh invitation without unique-key conflicts.
        fresh = Employee.objects.create(name='다시 등록', department=self.department)
        invite(fresh, self.user.email)
        self.assertEqual(Invitation.objects.get(employee=fresh).email, self.user.email)

    def test_business_history_blocks_complete_deletion_and_access_deletion_preserves_history(self):
        today = timezone.localdate()
        task = Task.objects.create(title='보존 업무', assignee=self.employee, start_date=today, due_date=today)
        event = TaskEvent.objects.create(task=task, author=self.user, author_name=self.user.email, kind='progress', body='보존 보고')
        with self.assertRaises(ValidationError):
            change_access(self.admin, self.employee.pk, 'delete_employee', '퇴사', self.user.email)
        self.assertTrue(get_user_model().objects.filter(pk=self.user.pk).exists())
        change_access(self.admin, self.employee.pk, 'delete_access', '퇴사 계정 삭제', self.user.email)
        task.refresh_from_db(); event.refresh_from_db(); self.employee.refresh_from_db()
        self.assertEqual(task.assignee_id, self.employee.pk)
        self.assertIsNone(event.author_id)
        self.assertEqual(event.body, '보존 보고')
        self.assertIsNone(self.employee.user_id)
        self.assertFalse(self.employee.is_active)
        self.assertFalse(Invitation.objects.filter(employee=self.employee).exists())

    def test_wrong_confirmation_or_missing_reason_never_mutates_account(self):
        for reason, confirmation in [('퇴사', 'wrong@gunsystems.co.kr'), ('', self.user.email)]:
            with self.assertRaises(ValidationError):
                change_access(self.admin, self.employee.pk, 'delete_employee', reason, confirmation)
        self.user.refresh_from_db(); self.employee.refresh_from_db()
        self.assertTrue(self.user.is_active)
        self.assertTrue(self.employee.is_active)
        self.assertTrue(Invitation.objects.filter(employee=self.employee).exists())

    def test_non_superuser_and_self_or_superuser_targets_are_protected(self):
        with self.assertRaises(PermissionDenied):
            change_access(self.user, self.employee.pk, 'suspend', '권한 우회')
        self_employee = Employee.objects.create(name='관리자', department=self.department, user=self.admin)
        for action in ['suspend', 'reactivate', 'delete_access', 'delete_employee']:
            with self.assertRaises(ValidationError):
                change_access(self.admin, self_employee.pk, action, '자기 계정 처리', self.admin.email)
        other_admin = get_user_model().objects.create_superuser('other-owner', password='test')
        with self.assertRaises(ValidationError):
            change_access(other_admin, self_employee.pk, 'suspend', '최상위 계정 중지')
        self.admin.refresh_from_db()
        self.assertTrue(self.admin.is_active)

    def test_management_page_permissions_post_confirmation_and_admin_link(self):
        url = f'/accounts/employees/{self.employee.pk}/manage/'
        self.assertContains(self.client.get('/admin/tasks/employee/'), url)
        self.assertContains(self.client.get(url), '보존할 업무 이력')
        self.assertEqual(self.employee_client().get(url).status_code, 403)
        response = self.client.post(url, {'action':'delete_employee','reason':'테스트','confirmation':'wrong'})
        self.assertContains(response, '삭제 확인이 만료되었거나 올바르지 않습니다')
        response = self.client.post(url, {'action':'suspend','reason':'퇴사'})
        self.assertRedirects(response, '/accounts/employees/', fetch_redirect_response=False)
        self.assertContains(self.client.get('/admin/tasks/employee/'), '사용 중지')

    def test_delete_popup_is_required_and_confirm_button_deletes_without_typing_name(self):
        url = f'/accounts/employees/{self.employee.pk}/manage/'
        self.assertNotContains(self.client.get(url), '삭제 확인 입력값')
        self.assertNotContains(self.client.get(url), 'id_confirmation')
        response = self.client.post(url, {'action':'delete_employee', 'reason':'테스트 재등록'})
        self.assertContains(response, '직원을 완전히 삭제할까요?')
        self.assertContains(response, 'id="cancel-delete"')
        self.assertTrue(Employee.objects.filter(pk=self.employee.pk).exists())
        self.assertTrue(get_user_model().objects.filter(pk=self.user.pk).exists())
        token = response.context['pending_delete']['token']
        # Closing or cancelling the popup never submits the final deletion.
        self.client.get(url)
        self.assertTrue(Employee.objects.filter(pk=self.employee.pk).exists())
        response = self.client.post(url, {'action':'delete_employee', 'reason':'테스트 재등록', 'confirmation':token})
        self.assertRedirects(response, '/accounts/employees/', fetch_redirect_response=False)
        self.assertFalse(Employee.objects.filter(pk=self.employee.pk).exists())
        self.assertFalse(get_user_model().objects.filter(pk=self.user.pk).exists())
        self.assertTrue(get_user_model().objects.filter(pk=self.admin.pk).exists())

    def test_delete_popup_confirmation_is_bound_to_session_action_reason_and_expires(self):
        url = f'/accounts/employees/{self.employee.pk}/manage/'
        response = self.client.post(url, {'action':'delete_employee', 'reason':'테스트'})
        token = response.context['pending_delete']['token']
        for action,reason in [('delete_access','테스트'), ('delete_employee','다른 사유')]:
            response = self.client.post(url, {'action':action, 'reason':reason, 'confirmation':token})
            self.assertContains(response, '처리 내용이 변경')
        other = Client()
        other.force_login(self.admin)
        self.assertContains(other.post(url, {'action':'delete_employee','reason':'테스트','confirmation':token}), '처리 내용이 변경')
        with patch('django.core.signing.time.time', return_value=time.time()+301):
            self.assertContains(self.client.post(url, {'action':'delete_employee','reason':'테스트','confirmation':token}), '만료되었거나')
        self.assertTrue(Employee.objects.filter(pk=self.employee.pk).exists())

    def test_history_blocks_popup_and_new_history_blocks_previously_confirmed_deletion(self):
        url = f'/accounts/employees/{self.employee.pk}/manage/'
        response = self.client.post(url, {'action':'delete_employee', 'reason':'테스트'})
        token = response.context['pending_delete']['token']
        today = timezone.localdate()
        Task.objects.create(title='보존 업무', assignee=self.employee, start_date=today, due_date=today)
        for confirmation in ['', token]:
            response = self.client.post(url, {'action':'delete_employee','reason':'테스트','confirmation':confirmation})
            self.assertContains(response, '담당 업무 1건')
            self.assertNotContains(response, 'id="delete-confirmation"')
        response = self.client.post(url, {'action':'delete_access', 'reason':'퇴사'})
        self.assertContains(response, '직원 기록과 업무 이력은 보존됩니다')
        token = response.context['pending_delete']['token']
        response = self.client.post(url, {'action':'delete_access','reason':'퇴사','confirmation':token})
        self.assertEqual(response.status_code, 302)
        self.assertTrue(Employee.objects.filter(pk=self.employee.pk).exists())
        self.assertTrue(Task.objects.filter(assignee_id=self.employee.pk).exists())
        self.assertFalse(get_user_model().objects.filter(pk=self.user.pk).exists())

    def test_management_post_requires_csrf_and_get_does_not_change_state(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.admin)
        url = f'/accounts/employees/{self.employee.pk}/manage/'
        self.assertEqual(client.get(url).status_code, 200)
        self.assertEqual(client.post(url, {'action':'suspend','reason':'위조'}).status_code, 403)
        self.employee.refresh_from_db()
        self.assertTrue(self.employee.is_active)

    def test_suspending_unactivated_employee_revokes_code_link_and_reinvitation(self):
        employee = Employee.objects.create(name='초대 대기', department=self.department)
        invite(employee, 'pending@gunsystems.co.kr')
        invitation = Invitation.objects.get(employee=employee)
        invitation.code_digest = make_password('123456')
        invitation.verified_session = 'old-browser'
        invitation.save()
        change_access(self.admin, employee.pk, 'suspend', '채용 취소')
        invitation.refresh_from_db(); employee.refresh_from_db()
        self.assertLessEqual(invitation.expires_at, timezone.now())
        self.assertEqual(invitation.code_digest, '')
        self.assertEqual(invitation.verified_session, '')
        with self.assertRaises(ValidationError):
            invite(employee, 'pending@gunsystems.co.kr')
        change_access(self.admin, employee.pk, 'reactivate', '재등록 허용')
        employee.refresh_from_db()
        invite(employee, 'pending@gunsystems.co.kr')
        invitation.refresh_from_db()
        self.assertGreater(invitation.expires_at, timezone.now())

    def test_employee_disabled_flag_blocks_even_if_user_is_manually_active(self):
        self.employee.is_active = False
        self.employee.save()
        member = self.employee_client()
        self.assertEqual(member.get('/dashboard/').status_code, 302)
        self.assertNotIn('_auth_user_id', member.session)
        member.post('/login/', {'username':self.user.email,'password':'member-password'})
        self.assertNotIn('_auth_user_id', member.session)

    def test_disabled_employees_are_not_offered_for_new_assignments_but_existing_work_is_editable(self):
        change_access(self.admin, self.employee.pk, 'suspend', '퇴사')
        self.assertFalse(TaskForm(user=self.admin).fields['assignee'].queryset.filter(pk=self.employee.pk).exists())
        today = timezone.localdate()
        task = Task.objects.create(title='과거 업무', assignee=self.employee, start_date=today, due_date=today)
        self.assertTrue(TaskForm(user=self.admin, instance=task).fields['assignee'].queryset.filter(pk=self.employee.pk).exists())

    def test_existing_reset_link_cannot_change_disabled_employee_password(self):
        token = default_token_generator.make_token(self.user)
        uid = urlsafe_base64_encode(force_bytes(self.user.pk))
        change_access(self.admin, self.employee.pk, 'suspend', '퇴사')
        anonymous = Client()
        response = anonymous.get(f'/accounts/password/reset/{uid}/{token}/')
        self.assertContains(response, '만료되었거나 사용할 수 없는 링크')
        anonymous.post('/accounts/password/reset/', {'email':self.user.email})
        self.assertEqual(len(mail.outbox), 0)

    def test_default_employee_delete_cannot_leave_orphan_login_credentials(self):
        response = self.client.get(f'/admin/tasks/employee/{self.employee.pk}/delete/')
        self.assertEqual(response.status_code, 403)
        self.assertNotContains(self.client.get('/admin/tasks/employee/'), '선택된 employees 을/를 삭제합니다.')
