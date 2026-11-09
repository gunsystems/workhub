import re
from datetime import timedelta
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase, Client, override_settings
from django.utils import timezone
from django_otp.plugins.otp_totp.models import TOTPDevice
from tasks.models import Employee, Department, HiworksDirectoryCache, HiworksLink
from .models import AccessProfile


@override_settings(DEBUG=False,SESSION_COOKIE_SECURE=True,CSRF_COOKIE_SECURE=True,
    SECURE_SSL_REDIRECT=True,SECURE_PROXY_SSL_HEADER=('HTTP_X_FORWARDED_PROTO','https'),
    WORKHUB_REQUIRE_HIWORKS=True,WORKHUB_SCHEDULER_ENABLED=False,
    PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class MembershipRetryTests(TestCase):
    def setUp(self):
        self.user=get_user_model().objects.create_user('retry-worker',email='retry@gunsystems.co.kr')
        self.emp=Employee.objects.create(name='검증 직원',department=Department.objects.create(name='재확인팀'),user=self.user)
        AccessProfile.objects.create(user=self.user,email=self.user.email,verified_at=timezone.now())
        self.device=TOTPDevice.objects.create(user=self.user,confirmed=True)
        self.row={'email':self.user.email,'remote_id':'verified-test-id','user_id':'retry','name':'검증 직원','department':'재확인팀'}
        HiworksLink.objects.create(employee=self.emp,email=self.user.email,remote_id=self.row['remote_id'],user_id=self.row['user_id'])
        self.cache=HiworksDirectoryCache.objects.create(checked_at=timezone.now(),rows=[])
        self.client=Client(enforce_csrf_checks=True);self.client.force_login(self.user)
        s=self.client.session;s['otp_device_id']=self.device.persistent_id;s.save()
        self.proxy={'HTTP_X_FORWARDED_PROTO':'https','HTTP_HOST':'testserver'}

    def blocked_page(self):
        response=self.client.get('/work/new/',**self.proxy)
        self.assertEqual(response.status_code,403)
        token=re.search(r'name="csrfmiddlewaretoken" value="([^"]+)"',response.content.decode()).group(1)
        return response,token

    def test_retry_from_blocked_page_without_origin_passes_https_csrf(self):
        page,token=self.blocked_page()
        # Simulate the browser's provenance policy, not an invented Origin header.
        headers={} if page['Referrer-Policy']=='no-referrer' else {'HTTP_REFERER':'https://testserver/work/new/'}
        with patch('tasks.hiworks.directory_rows',return_value=[self.row]) as query:
            result=self.client.post('/accounts/hiworks/access/',{'csrfmiddlewaretoken':token},**self.proxy,**headers)
        self.assertEqual(result.status_code,302);query.assert_called_once()
        self.assertEqual(self.client.get('/work/new/',**self.proxy).status_code,200)

    def test_logout_from_blocked_page_without_origin_works(self):
        page,token=self.blocked_page()
        headers={} if page['Referrer-Policy']=='no-referrer' else {'HTTP_REFERER':'https://testserver/work/new/'}
        r=self.client.post('/logout/',{'csrfmiddlewaretoken':token},**self.proxy,**headers)
        self.assertEqual(r.status_code,302);self.assertNotIn('_auth_user_id',self.client.session)

    def test_stale_cache_refreshes_before_rejecting_valid_employee(self):
        self.cache.checked_at=timezone.now()-timedelta(minutes=6);self.cache.save()
        with patch('tasks.hiworks.directory_rows',return_value=[self.row]) as query:
            r=self.client.get('/work/new/',**self.proxy)
        self.assertEqual(r.status_code,200);query.assert_called_once()

    def test_directory_failure_still_blocks_instead_of_bypassing_membership(self):
        self.cache.checked_at=timezone.now()-timedelta(minutes=6);self.cache.save()
        with patch('tasks.hiworks.directory_rows',side_effect=ValidationError('하이웍스 조회 실패')):
            r=self.client.get('/work/new/',**self.proxy)
        self.assertEqual(r.status_code,403);self.assertContains(r,'회사 직원 등록 확인',status_code=403)

    def test_foreign_origin_is_still_rejected(self):
        _,token=self.blocked_page()
        r=self.client.post('/accounts/hiworks/access/',{'csrfmiddlewaretoken':token},HTTP_ORIGIN='https://foreign.invalid',**self.proxy)
        self.assertEqual(r.status_code,403)

    def test_error_recovery_get_redirects_instead_of_405_or_mutation(self):
        with patch('accounts.access_views.refresh_directory') as refresh:
            r=self.client.get('/accounts/hiworks/access/',**self.proxy)
        self.assertEqual(r.status_code,302);self.assertEqual(r['Location'],'/dashboard/')
        refresh.assert_not_called()

    def test_scheduler_refresh_precedes_unrelated_recurrence_failure(self):
        from tasks.scheduler import tick
        with patch('accounts.hiworks_access.refresh_directory') as refresh, patch('tasks.models.WorkSeries.objects.filter') as rules, patch('tasks.work_series.process_work_rule',side_effect=RuntimeError('unrelated')):
            rules.return_value.values_list.return_value=[999]
            with self.assertRaises(RuntimeError):tick(sync_google=False)
        refresh.assert_called_once()
