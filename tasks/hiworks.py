"""Explicit directory review and one-attempt notification outbox."""
import json
from urllib.request import Request, build_opener
from urllib.error import HTTPError, URLError
from datetime import timedelta
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.utils import timezone
from accounts.mail_backend import NoRedirect
from .models import HiworksLink, HiworksDelivery
from .permissions import visible_tasks


def api(path, token, data=None):
    if not token or any(c in token for c in '\r\n'):
        raise ValidationError('서버에 해당 기능의 하이웍스 토큰을 설정해 주세요.')
    body = json.dumps(data).encode('utf-8') if data is not None else None
    request = Request('https://api.hiworks.com' + path, data=body, headers={
        'Authorization': 'Bearer ' + token, 'Accept': 'application/json',
        'Content-Type': 'application/json'})
    try:
        with build_opener(NoRedirect()).open(request, timeout=12) as response:
            raw = response.read(2_000_001)
            if len(raw) > 2_000_000: raise ValueError()
            result = json.loads(raw)
    except HTTPError as exc:
        raise ValidationError(f'하이웍스 요청이 거절되었습니다 (HTTP {exc.code}). 권한과 허용 IP를 확인해 주세요.') from None
    except (URLError, OSError, ValueError, TypeError):
        raise ValidationError('하이웍스 응답을 확인하지 못했습니다. 발송은 자동 재시도하지 않습니다.') from None
    if path == '/hrm/v2/organizations' and isinstance(result, dict) and 'node_id' in result and 'code' not in result:
        return result
    if not isinstance(result, dict) or result.get('code') != 'SUC':
        raise ValidationError('하이웍스에서 성공 응답을 받지 못했습니다. 토큰의 사용 범위를 확인해 주세요.')
    return result.get('result')


def directory_rows():
    # Request only after an administrator explicitly selects preview.
    data = api('/hrm/v2/organizations', settings.HIWORKS_DIRECTORY_TOKEN)
    found = []
    def walk(value, department=''):
        if isinstance(value, list):
            for node in value: walk(node, department)
        elif isinstance(value, dict):
            if 'node_id' in value and 'user_no' not in value:
                department = str(value.get('name') or '')[:80]
            mail_id = str(value.get('mail_id') or '').strip()
            # HR email is a contact address and may be shared by multiple users.
            # Only the office mail ID identifies the employee's company mailbox.
            email = (mail_id if '@' in mail_id else mail_id+'@'+settings.WORKHUB_EMAIL_DOMAIN).lower() if mail_id else ''
            try: validate_email(email)
            except ValidationError: email = ''
            remote_id = str(value.get('user_no') or value.get('id') or '').strip()
            mail_id = str(value.get('mail_id') or '').strip()
            name = str(value.get('name') or '').strip()
            if email.endswith('@' + settings.WORKHUB_EMAIL_DOMAIN) and remote_id and mail_id and name:
                found.append({'email': email, 'remote_id': remote_id[:150], 'user_id': mail_id.split('@',1)[0].lower()[:150], 'name': name[:80], 'department':department})
            else:
                for child in value.values():
                    if isinstance(child, (dict, list)): walk(child, department)
    walk(data)
    if len(found) > 1000 or len({r['email'] for r in found}) != len(found) or len({r['remote_id'] for r in found}) != len(found):
        raise ValidationError('조회된 직원 정보가 중복되거나 너무 많아 적용하지 않았습니다.')
    if not found:
        raise ValidationError('매칭 가능한 회사 이메일·사용자 ID를 확인하지 못했습니다. 실제 응답 형식 확인이 필요합니다.')
    return found


def enqueue(notification):
    if settings.HIWORKS_NOTIFY_TOKEN and HiworksLink.objects.filter(employee__user_id=notification.user_id, employee__is_active=True, notifications_enabled=True).exists():
        HiworksDelivery.objects.get_or_create(notification=notification)


def deliver_pending():
    if not settings.HIWORKS_NOTIFY_TOKEN: return
    # Reserve capacity for authentication mail sharing the office API quota.
    if HiworksDelivery.objects.filter(attempted_at__date=timezone.localdate()).count() >= 200: return
    # Rows claimed before network I/O; ambiguous responses are never automatically replayed.
    for pk in HiworksDelivery.objects.filter(state='pending').order_by('pk').values_list('pk', flat=True)[:20]:
        if HiworksDelivery.objects.filter(attempted_at__date=timezone.localdate()).count() >= 200: break
        if not HiworksDelivery.objects.filter(pk=pk, state='pending').update(state='sending', attempted_at=timezone.now()): continue
        delivery = HiworksDelivery.objects.select_related('notification__user','notification__task').get(pk=pk)
        notification = delivery.notification
        link = HiworksLink.objects.filter(employee__user=notification.user, employee__is_active=True, notifications_enabled=True).first()
        if not link or not notification.user.is_active or not visible_tasks(notification.user).filter(pk=notification.task_id).exists() or notification.created_at < timezone.now()-timedelta(days=1):
            delivery.state='skipped'; delivery.detail='현재 접근 권한 또는 알림 설정이 없어 발송하지 않았습니다.'
        else:
            try:
                api('/office/v2/notify', settings.HIWORKS_NOTIFY_TOKEN, {
                    'user_list': [link.user_id], 'message': 'Workhub: 확인할 업무 알림이 있습니다.',
                    'link': settings.WORKHUB_BASE_URL.rstrip('/')+f'/tasks/{notification.task_id}/', 'solution_name':'GUNSYSTEMS Workhub'})
                delivery.state='sent'; delivery.detail='하이웍스 접수 완료'
            except ValidationError as exc:
                delivery.state='failed'; delivery.detail=exc.messages[0][:200]
        delivery.save(update_fields=['state','detail'])
