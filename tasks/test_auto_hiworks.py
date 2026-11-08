from datetime import timedelta
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.utils import timezone
from django_otp.plugins.otp_totp.models import TOTPDevice
from accounts.models import AccessProfile, Invitation
from .models import Department, Employee, HiworksLink, HiworksSyncStatus, SchedulerLease
from .hiworks import sync_automatic_links,request_auto_sync

@override_settings(HIWORKS_DIRECTORY_TOKEN='test-directory',HIWORKS_NOTIFY_TOKEN='test-notify',PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class AutoLinkTests(TestCase):
    def setUp(self):
        self.user=get_user_model().objects.create_user('lee@gunsystems.co.kr',email='lee@gunsystems.co.kr')
        self.department=Department.objects.create(name='자동연동 테스트')
        self.employee=Employee.objects.create(user=self.user,name='직원',department=self.department)
        self.access=AccessProfile.objects.create(user=self.user,email=self.user.email,verified_at=timezone.now(),require_mfa=False)
        self.invitation=Invitation.objects.create(employee=self.employee,email=self.user.email,token_digest='x'*64,expires_at=timezone.now()+timedelta(days=1),used_at=timezone.now())
        self.device=TOTPDevice.objects.create(user=self.user,confirmed=True)
        self.row={'remote_id':'123','user_id':'lee','email':self.user.email,'name':'직원','department':'다른 부서'}

    @patch('tasks.hiworks.directory_rows')
    def test_verified_signup_auto_links_and_enables_notifications_without_changing_employee(self, rows):
        rows.return_value=[self.row]
        sync_automatic_links();sync_automatic_links()
        link=HiworksLink.objects.get(employee=self.employee)
        self.assertTrue(link.notifications_enabled);self.assertTrue(link.auto_configured)
        self.assertEqual(rows.call_count,1)
        self.employee.refresh_from_db();self.assertEqual(self.employee.department,self.department)
        self.assertFalse(self.user.is_staff)

    @patch('tasks.hiworks.directory_rows')
    def test_unverified_or_unfinished_onboarding_never_links(self, rows):
        rows.return_value=[self.row]
        self.device.confirmed=False;self.device.save()
        sync_automatic_links();rows.assert_not_called()
        self.device.confirmed=True;self.device.save()
        self.access.verified_at=None;self.access.save()
        sync_automatic_links();rows.assert_not_called()
        self.access.verified_at=timezone.now();self.access.save()
        self.invitation.used_at=None;self.invitation.save()
        sync_automatic_links();rows.assert_not_called()
        self.assertFalse(HiworksLink.objects.exists())

    @patch('tasks.hiworks.directory_rows')
    def test_same_name_foreign_email_and_legacy_admin_not_matched(self, rows):
        rows.return_value=[{**self.row,'email':'another@gunsystems.co.kr'}]
        sync_automatic_links()
        self.assertFalse(HiworksLink.objects.exists())
        self.assertEqual(HiworksSyncStatus.objects.get().status,'attention')
        self.user.is_superuser=True;self.user.save();request_auto_sync();sync_automatic_links()
        self.assertEqual(rows.call_count,1)

    @patch('tasks.hiworks.directory_rows')
    def test_existing_test_employee_becomes_automatic_but_manual_optout_is_respected(self, rows):
        rows.return_value=[self.row]
        link=HiworksLink.objects.create(employee=self.employee,remote_id='123',user_id='lee',email=self.user.email)
        sync_automatic_links();link.refresh_from_db();self.assertTrue(link.notifications_enabled)
        link.notifications_enabled=False;link.save();request_auto_sync();sync_automatic_links()
        link.refresh_from_db();self.assertFalse(link.notifications_enabled);self.assertEqual(rows.call_count,0)

    @patch('tasks.hiworks.directory_rows')
    def test_conflicting_existing_remote_identity_not_rebound(self, rows):
        rows.return_value=[self.row]
        link=HiworksLink.objects.create(employee=self.employee,remote_id='different',user_id='different',email=self.user.email)
        sync_automatic_links();link.refresh_from_db()
        self.assertEqual(link.remote_id,'different');self.assertFalse(link.notifications_enabled)
        self.assertEqual(HiworksSyncStatus.objects.get().status,'attention')

    @patch('tasks.hiworks.directory_rows')
    def test_provider_failure_is_recorded_and_throttled(self, rows):
        rows.side_effect=ValidationError('허용 IP 확인 필요')
        sync_automatic_links();sync_automatic_links()
        self.assertEqual(rows.call_count,1)
        self.assertEqual(HiworksSyncStatus.objects.get().status,'error')
        self.assertFalse(HiworksLink.objects.exists())
        request_auto_sync();rows.side_effect=None;rows.return_value=[self.row]
        sync_automatic_links();self.assertTrue(HiworksLink.objects.get().notifications_enabled)
