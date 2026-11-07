import re
from urllib.parse import urlparse

from django.core import mail
from django.test import Client, TestCase, override_settings
from tasks.models import Department, Employee
from .services import invite


@override_settings(DEBUG=False, SESSION_COOKIE_SECURE=True, CSRF_COOKIE_SECURE=True,
                   SECURE_SSL_REDIRECT=True,
                   SECURE_PROXY_SSL_HEADER=('HTTP_X_FORWARDED_PROTO', 'https'),
                   EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
                   PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class ProductionCSRFFlowTests(TestCase):
    def setUp(self):
        department = Department.objects.create(name='HTTPS 인증 테스트')
        employee = Employee.objects.create(name='인증 테스트 직원', department=department)
        invite(employee, 'csrf-test@gunsystems.co.kr')
        html = mail.outbox[-1].alternatives[0].content
        url = re.search(r'href="(https://[^\"]+/accounts/activate/[^\"]+)"', html).group(1)
        self.path = urlparse(url).path
        self.client = Client(enforce_csrf_checks=True)
        self.proxy = {'HTTP_X_FORWARDED_PROTO':'https', 'HTTP_HOST':'testserver'}

    def open_form(self):
        response = self.client.get(self.path, **self.proxy)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Referrer-Policy'], 'same-origin')
        self.assertIn('no-store', response['Cache-Control'])
        self.assertTrue(response.cookies['csrftoken']['secure'])
        return re.search(r'name="csrfmiddlewaretoken" value="([^"]+)"', response.content.decode()).group(1)

    def test_https_proxy_code_send_with_origin_and_csrf_cookie(self):
        token = self.open_form()
        response = self.client.post(self.path, {'action':'send','csrfmiddlewaretoken':token},
                                    HTTP_ORIGIN='https://testserver', **self.proxy)
        self.assertContains(response, '회사 이메일로 인증 코드를 보냈습니다.')
        self.assertEqual(len(mail.outbox), 2)

    def test_https_proxy_code_send_with_same_origin_referer_without_origin(self):
        token = self.open_form()
        response = self.client.post(self.path, {'action':'send','csrfmiddlewaretoken':token},
                                    HTTP_REFERER='https://testserver'+self.path, **self.proxy)
        self.assertContains(response, '회사 이메일로 인증 코드를 보냈습니다.')
        code = re.search(r'인증 코드: (\d{6})', mail.outbox[-1].body).group(1)
        token = re.search(r'name="csrfmiddlewaretoken" value="([^"]+)"', response.content.decode()).group(1)
        response = self.client.post(self.path, {'code':code,'csrfmiddlewaretoken':token},
                                    HTTP_REFERER='https://testserver'+self.path, **self.proxy)
        self.assertEqual(response.status_code, 302)
        self.assertContains(self.client.get(self.path, **self.proxy), '비밀번호 설정')

    def test_foreign_origin_and_bad_token_remain_blocked(self):
        token = self.open_form()
        for origin, value in [('https://other.example', token), ('null', token), ('https://testserver','Z'*64)]:
            response = self.client.post(self.path, {'action':'send','csrfmiddlewaretoken':value},
                                        HTTP_ORIGIN=origin, **self.proxy)
            self.assertEqual(response.status_code, 403)
            self.assertContains(response, '화면 다시 열기', status_code=403)
            self.assertNotContains(response, 'DEBUG', status_code=403)
        self.assertEqual(len(mail.outbox), 1)

    def test_missing_provenance_or_cookie_still_blocked_and_get_recovers(self):
        token = self.open_form()
        self.assertEqual(self.client.post(self.path, {'action':'send','csrfmiddlewaretoken':token}, **self.proxy).status_code, 403)
        other = Client(enforce_csrf_checks=True)
        self.assertEqual(other.post(self.path, {'action':'send','csrfmiddlewaretoken':token},
                                    HTTP_ORIGIN='https://testserver', **self.proxy).status_code, 403)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(self.client.get(self.path, **self.proxy).status_code, 200)

    def test_login_uses_compatible_private_referrer_policy(self):
        response = self.client.get('/login/', **self.proxy)
        self.assertEqual(response['Referrer-Policy'], 'same-origin')
