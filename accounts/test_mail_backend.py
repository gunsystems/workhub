import json
from io import BytesIO
from unittest.mock import patch, MagicMock
from urllib.error import HTTPError, URLError

from django.core import mail
from django.core.exceptions import ValidationError
from django.test import SimpleTestCase, override_settings
from .mail_backend import HiworksEmailBackend, NoRedirect, ENDPOINT


@override_settings(EMAIL_BACKEND='accounts.mail_backend.HiworksEmailBackend',
                   HIWORKS_OFFICE_TOKEN='test-token-not-a-real-secret', HIWORKS_USER_ID='admin')
class HiworksMailTests(SimpleTestCase):
    def response(self, data):
        response = MagicMock()
        response.__enter__.return_value = response
        response.read.return_value = json.dumps(data).encode()
        return response

    def send(self):
        return mail.send_mail('초대 메일', '<script>bad</script>\nhttps://example.com/activate/token/',
                              'admin@gunsystems.co.kr', ['lee@gunsystems.co.kr'])

    @patch('accounts.mail_backend.build_opener')
    def test_multipart_office_token_sender_and_recipient_success(self, build):
        build.return_value.open.return_value = self.response({'code':'SUC','result':{'successList':['lee@gunsystems.co.kr']}})
        self.assertEqual(self.send(), 1)
        request = build.return_value.open.call_args.args[0]
        self.assertEqual(request.full_url, ENDPOINT)
        self.assertEqual(request.get_header('Authorization'), 'Bearer test-token-not-a-real-secret')
        self.assertTrue(request.get_header('Content-type').startswith('multipart/form-data; boundary='))
        payload = request.data.decode()
        self.assertIn('name="user_id"\r\n\r\nadmin', payload)
        self.assertIn('name="save_sent_mail"\r\n\r\nN', payload)
        self.assertIn('name="to"\r\n\r\nlee@gunsystems.co.kr', payload)
        self.assertIn('&lt;script&gt;', payload)
        self.assertNotIn('<script>', payload)
        self.assertIn('href="https://example.com/activate/token/"', payload)
        self.assertNotIn('test-token', payload)

    @patch('accounts.mail_backend.build_opener')
    def test_http_200_is_not_sufficient(self, build):
        for data in [{'code':'ERR','message':'private provider error'}, {'code':'SUC'},
                     {'code':'SUC','result':{'successList':['other@gunsystems.co.kr']}},
                     {'code':'SUC','result':{'wrongList':['lee@gunsystems.co.kr']}}, []]:
            build.return_value.open.return_value = self.response(data)
            with self.assertRaises(ValidationError) as caught:
                self.send()
            self.assertNotIn('private provider', str(caught.exception))

    @patch('accounts.mail_backend.build_opener')
    def test_timeout_http_failure_and_bad_json_are_sanitized_and_not_retried(self, build):
        for error in [URLError('private details'), TimeoutError('private details'),
                      HTTPError(ENDPOINT, 403, 'private details', {}, BytesIO())]:
            build.reset_mock()
            build.return_value.open.side_effect = error
            with self.assertRaises(ValidationError) as caught:
                self.send()
            self.assertNotIn('private details', str(caught.exception))
            self.assertEqual(build.return_value.open.call_count, 1)
        build.return_value.open.side_effect = None
        build.return_value.open.return_value.__enter__.return_value.read.return_value = b'not-json'
        with self.assertRaises(ValidationError):
            self.send()

    @patch('accounts.mail_backend.build_opener')
    @override_settings(HIWORKS_OFFICE_TOKEN='')
    def test_missing_credentials_never_call_network(self, build):
        with self.assertRaises(ValidationError):
            self.send()
        build.assert_not_called()

    def test_redirects_cannot_forward_bearer_token(self):
        self.assertIsNone(NoRedirect().redirect_request(None, None, 302, '', {}, 'https://other.example/'))

    @patch('accounts.mail_backend.build_opener')
    def test_unsupported_message_and_fail_silently(self, build):
        backend = HiworksEmailBackend(fail_silently=True)
        message = mail.EmailMessage('test', 'test', to=['a@gunsystems.co.kr', 'b@gunsystems.co.kr'])
        self.assertEqual(backend.send_messages([message]), 0)
        build.assert_not_called()

    @patch('accounts.mail_backend.build_opener')
    def test_designed_html_alternative_is_sent_without_being_escaped(self, build):
        build.return_value.open.return_value = self.response({'code':'SUC','result':{'successList':['lee@gunsystems.co.kr']}})
        message = mail.EmailMultiAlternatives('초대', '일반 텍스트', 'admin@gunsystems.co.kr', ['lee@gunsystems.co.kr'])
        message.attach_alternative('<table role="presentation"><tr><td>회사 초대</td></tr></table>', 'text/html')
        self.assertEqual(message.send(), 1)
        payload = build.return_value.open.call_args.args[0].data.decode()
        self.assertIn('<table role="presentation">', payload)
        self.assertNotIn('&lt;table', payload)
