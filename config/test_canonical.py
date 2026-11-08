from django.http import HttpResponse
from django.test import SimpleTestCase,RequestFactory,override_settings
from .canonical import CompanyDomainMiddleware
from tasks.google_calendar import callback_url
from django.test import TestCase
from django.contrib.auth import get_user_model
from django.core import mail
from accounts.services import invite
from accounts.models import AccessProfile
from django.utils import timezone
from tasks.models import Department,Employee


@override_settings(WORKHUB_BASE_URL='https://workhub.gunsystems.co.kr',ALLOWED_HOSTS=['testserver','workhub.gunsystems.co.kr','gunsystems-workhub-test.onrender.com'])
class CompanyDomainTests(SimpleTestCase):
    def setUp(self):
        self.factory=RequestFactory()
        self.middleware=CompanyDomainMiddleware(lambda request:HttpResponse('original handler'))

    def test_legacy_link_preserves_path_and_query_without_redirect_loop(self):
        request=self.factory.get('/projects/?state=active',HTTP_HOST='gunsystems-workhub-test.onrender.com')
        response=self.middleware(request)
        self.assertEqual(response.status_code,302)
        self.assertEqual(response['Location'],'https://workhub.gunsystems.co.kr/projects/?state=active')
        request=self.factory.get('/projects/',HTTP_HOST='workhub.gunsystems.co.kr')
        self.assertEqual(self.middleware(request).content,b'original handler')

    def test_old_password_or_business_post_is_not_replayed_or_processed(self):
        request=self.factory.post('/login/',{'password':'private-test-value'},HTTP_HOST='gunsystems-workhub-test.onrender.com')
        response=self.middleware(request)
        self.assertEqual(response.status_code,409)
        self.assertIn('저장되지 않았습니다',response.content.decode())
        self.assertNotIn('private-test-value',response.content.decode())

    def test_health_static_and_local_requests_remain_available(self):
        for path,host in [('/healthz/','gunsystems-workhub-test.onrender.com'),('/static/tasks/style.css','gunsystems-workhub-test.onrender.com'),('/login/','testserver')]:
            self.assertEqual(self.middleware(self.factory.get(path,HTTP_HOST=host)).status_code,200)

    @override_settings(WORKHUB_BASE_URL='https://gunsystems-workhub-test.onrender.com')
    def test_rollback_to_original_base_disables_redirect(self):
        self.assertEqual(self.middleware(self.factory.get('/login/',HTTP_HOST='gunsystems-workhub-test.onrender.com')).status_code,200)

    def test_google_redirect_uses_company_url(self):
        self.assertEqual(callback_url(),'https://workhub.gunsystems.co.kr/work/calendar/google/callback/')


@override_settings(WORKHUB_BASE_URL='https://workhub.gunsystems.co.kr',EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class CompanyEmailLinkTests(TestCase):
    def test_invitation_and_reset_email_links_use_company_domain(self):
        department=Department.objects.create(name='도메인 확인 팀')
        employee=Employee.objects.create(name='초대 확인 직원',department=department)
        invite(employee,'domain-test@gunsystems.co.kr')
        self.assertIn('href="https://workhub.gunsystems.co.kr/accounts/activate/',mail.outbox[-1].alternatives[0].content)
        user=get_user_model().objects.create_user(username='domain-reset',email='reset-test@gunsystems.co.kr',password='test-only-pass-731')
        AccessProfile.objects.create(user=user,email=user.email,verified_at=timezone.now())
        response=self.client.post('/accounts/password/reset/',{'email':'reset-test@gunsystems.co.kr'})
        self.assertEqual(response.status_code,302)
        self.assertIn('https://workhub.gunsystems.co.kr/accounts/password/reset/',mail.outbox[-1].alternatives[0].content)
