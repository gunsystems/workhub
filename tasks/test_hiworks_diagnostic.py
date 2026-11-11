from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase, override_settings
from django.utils import timezone
from accounts.models import AccessProfile
from .models import Department, Employee, HiworksLink, SystemAudit
from .hiworks import test_notification as send_test


@override_settings(WORKHUB_REQUIRE_HIWORKS=False,WORKHUB_SCHEDULER_ENABLED=False,
    HIWORKS_NOTIFY_TOKEN='test-only',PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class MessengerDiagnosticTests(TestCase):
    def setUp(self):
        self.admin=get_user_model().objects.create_superuser('operator')
        self.user=get_user_model().objects.create_user('lee',email='lee@gunsystems.co.kr')
        AccessProfile.objects.create(user=self.user,email=self.user.email,verified_at=timezone.now(),require_mfa=False)
        self.employee=Employee.objects.create(name='직원',user=self.user,department=Department.objects.create(name='팀'))
        self.link=HiworksLink.objects.create(employee=self.employee,email=self.user.email,user_id='lee',remote_id='123')
        self.row={'email':self.user.email,'user_id':'lee','remote_id':'123'}

    @patch('tasks.hiworks.api',return_value={'code':'SUC'})
    @patch('tasks.hiworks.directory_rows')
    def test_single_fresh_recipient_and_duplicate_guard(self,rows,api):
        rows.return_value=[self.row]
        result=send_test(self.link.pk,self.admin)
        self.assertIn('수신 ID lee',result)
        self.assertEqual(api.call_args.args[2]['user_list'],['lee'])
        self.assertIn('mlink',api.call_args.args[2])
        with self.assertRaises(ValidationError):send_test(self.link.pk,self.admin)
        self.assertEqual(api.call_count,1)
        self.assertEqual(SystemAudit.objects.filter(action='hiworks_test',after__status='accepted').count(),1)

    @patch('tasks.hiworks.api')
    @patch('tasks.hiworks.directory_rows')
    def test_permissions_and_stale_identity_never_send(self,rows,api):
        with self.assertRaises(PermissionDenied):send_test(self.link.pk,self.user)
        rows.return_value=[{**self.row,'user_id':'someone-else'}]
        with self.assertRaises(ValidationError):send_test(self.link.pk,self.admin)
        api.assert_not_called()

    @patch('tasks.hiworks.api',side_effect=ValidationError('하이웍스 요청 거절 HTTP 403'))
    @patch('tasks.hiworks.directory_rows')
    def test_failure_is_audited_without_replay(self,rows,api):
        rows.return_value=[self.row]
        with self.assertRaises(ValidationError):send_test(self.link.pk,self.admin)
        self.assertEqual(SystemAudit.objects.filter(action='hiworks_test',after__status='unconfirmed').count(),1)
        with self.assertRaises(ValidationError):send_test(self.link.pk,self.admin)
        self.assertEqual(api.call_count,1)
