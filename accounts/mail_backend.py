"""Hiworks Office Token mail transport. Secrets never enter URLs or error text."""
import json
import secrets
from urllib.error import HTTPError, URLError
from urllib.request import Request, build_opener, HTTPRedirectHandler

from django.conf import settings
from django.core.mail.backends.base import BaseEmailBackend
from django.core.exceptions import ValidationError
from django.utils.html import urlize


ENDPOINT = 'https://api.hiworks.com/office/v2/webmail/sendMail'


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Never forward a bearer token to a redirect destination.
        return None


class HiworksEmailBackend(BaseEmailBackend):
    def send_messages(self, email_messages):
        count = 0
        for message in email_messages:
            try:
                self.send_message(message)
            except ValidationError:
                if not self.fail_silently:
                    raise
            else:
                count += 1
        return count

    def send_message(self, message):
        token = settings.HIWORKS_OFFICE_TOKEN
        user_id = settings.HIWORKS_USER_ID
        if not token or not user_id or any(c in token for c in '\r\n'):
            raise ValidationError('하이웍스 API 토큰과 발신 사용자 ID를 서버 비밀 설정에서 확인하세요.')
        recipients = message.recipients()
        if len(recipients) != 1 or message.cc or message.bcc or message.attachments:
            raise ValidationError('인증 메일은 첨부 없이 한 명에게만 발송합니다.')
        boundary = 'workhub-' + secrets.token_hex(16)
        fields = {
            'to': recipients[0], 'user_id': user_id, 'subject': message.subject,
            'content': '<div style="white-space:pre-wrap">' + str(urlize(message.body, autoescape=True)) + '</div>',
            # Do not retain live authentication codes or invitation links in Sent mail.
            'save_sent_mail': 'N',
        }
        parts = []
        for key, value in fields.items():
            parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n')
        payload = (''.join(parts) + f'--{boundary}--\r\n').encode('utf-8')
        request = Request(ENDPOINT, data=payload, method='POST', headers={
            'Authorization': 'Bearer ' + token,
            'Content-Type': 'multipart/form-data; boundary=' + boundary,
            'Accept': 'application/json',
        })
        try:
            with build_opener(NoRedirect()).open(request, timeout=settings.EMAIL_TIMEOUT) as response:
                raw = response.read(65537)
                if len(raw) > 65536:
                    raise ValueError('Oversized response')
                data = json.loads(raw)
        except HTTPError as exc:
            raise ValidationError(f'하이웍스 메일 API가 요청을 거절했습니다 (HTTP {exc.code}).') from None
        except (URLError, OSError, ValueError, TypeError):
            raise ValidationError('하이웍스 메일 API 응답을 확인하지 못했습니다. 중복 발송 방지를 위해 자동 재시도하지 않습니다.') from None
        if not isinstance(data, dict):
            raise ValidationError('하이웍스 메일 API 응답 형식이 올바르지 않습니다.')
        result = data.get('result')
        accepted = result.get('successList', []) if isinstance(result, dict) else []
        if (data.get('code') != 'SUC' or not isinstance(accepted, list)
                or recipients[0].casefold() not in [item.casefold() for item in accepted if isinstance(item, str)]):
            # Never surface the provider's raw response, which may contain account data.
            raise ValidationError('하이웍스 메일 API가 수신자 발송을 접수하지 않았습니다. 토큰 권한·허용 IP·발신 계정을 확인하세요.')
