"""Explicit directory review and one-attempt notification outbox."""
import json
import secrets
import re
from urllib.request import Request, build_opener
from urllib.error import HTTPError, URLError
from datetime import timedelta
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.utils import timezone
from django.db import transaction, IntegrityError
from ipaddress import IPv4Address
from accounts.mail_backend import NoRedirect
from .models import HiworksLink, HiworksDelivery, HiworksSyncStatus, SchedulerLease, Employee
from .permissions import visible_tasks


def api(path, token, data=None, *, full_response=False):
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
        message = '하이웍스 응답을 확인하지 못했습니다. '
        message += '중복 발송 방지를 위해 자동 재발송하지 않습니다.' if data is not None else '잠시 후 다시 확인해 주세요.'
        raise ValidationError(message) from None
    if path == '/hrm/v2/organizations' and isinstance(result, dict) and 'node_id' in result and 'code' not in result:
        return result
    if isinstance(result, dict) and result.get('code') != 'SUC':
        match = re.search(r'접근 IP\s*:\s*([0-9.]+)', str(result.get('message','')))
        if match:
            try: ip = str(IPv4Address(match.group(1)))
            except ValueError: ip = None
            if ip: raise ValidationError(f'하이웍스 토큰의 허용 IP에 {ip}를 추가해 주세요. 서버 재배포는 필요 없습니다.')
    if not isinstance(result, dict) or result.get('code') != 'SUC':
        raise ValidationError('하이웍스에서 성공 응답을 받지 못했습니다. 토큰의 사용 범위를 확인해 주세요.')
    return result if full_response else result.get('result')


def test_notification(link_id, actor):
    """One explicitly requested message; fresh directory identity, no queue replay."""
    from .permissions import is_company_admin
    from django.core.exceptions import PermissionDenied
    from .audit import write
    if not is_company_admin(actor): raise PermissionDenied
    link = HiworksLink.objects.select_related('employee__user__workhub_access').get(pk=link_id)
    employee = link.employee
    user = employee.user
    access = getattr(user, 'workhub_access', None)
    if not (employee.is_active and user and user.is_active and access and access.verified_at
            and link.email.lower() == user.email.lower() == access.email.lower()):
        raise ValidationError('현재 재직·계정·인증 이메일을 확인할 수 없습니다.')
    rows = [r for r in directory_rows() if r['email'].lower() == link.email.lower()]
    if len(rows) != 1 or any(rows[0][key] != getattr(link,key) for key in ['remote_id','user_id']):
        raise ValidationError('현재 하이웍스 직원 ID와 저장된 연결이 다릅니다. 발송하지 않았습니다.')
    now = timezone.now()
    key = f'hiworks-test-{link.pk}'
    SchedulerLease.objects.get_or_create(key=key, defaults={'expires_at':now})
    if not SchedulerLease.objects.filter(key=key,expires_at__lte=now).update(expires_at=now+timedelta(minutes=1)):
        raise ValidationError('중복 방지를 위해 같은 직원의 테스트는 1분 후 다시 진행하세요.')
    label = f'Workhub 연결 확인 {timezone.localtime(now):%H:%M:%S}'
    write('hiworks_test','HiworksLink',link.pk,label,actor=actor,
          after={'recipient_email':link.email,'recipient_id':link.user_id,'status':'attempting'})
    try:
        response = api('/office/v2/notify',settings.HIWORKS_NOTIFY_TOKEN,{
            'user_list':[link.user_id], 'message':label+' · 선택한 직원 1명에게 보내는 연결 확인 알림입니다.',
            'link':settings.WORKHUB_BASE_URL.rstrip('/')+'/work/notices/',
            'mlink':settings.WORKHUB_BASE_URL.rstrip('/')+'/work/notices/',
            'solution_name':'GUNSYSTEMS Workhub'},full_response=True)
    except ValidationError as exc:
        write('hiworks_test','HiworksLink',link.pk,label,actor=actor,
              after={'recipient_id':link.user_id,'status':'unconfirmed','result':exc.messages[0]})
        raise
    write('hiworks_test','HiworksLink',link.pk,label,actor=actor,
          after={'recipient_email':link.email,'recipient_id':link.user_id,'status':'accepted','provider_code':response['code']})
    return f'{employee.name} · {link.email} · 수신 ID {link.user_id} · 하이웍스 응답 {response["code"]} · {label}. 실제 메신저 수신 여부는 별도로 확인해 주세요.'


def directory_rows():
    # Used by administrator previews and verified invitation onboarding.
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


def eligible_auto_employees():
    from django_otp.plugins.otp_totp.models import TOTPDevice
    confirmed = TOTPDevice.objects.filter(confirmed=True).values('user_id')
    candidates = Employee.objects.filter(is_active=True, user__is_active=True, user__is_superuser=False,
        user_id__in=confirmed, invitation__used_at__isnull=False,
        user__workhub_access__verified_at__isnull=False,
        user__workhub_access__otp_reset_key='').select_related('user__workhub_access','invitation','department')
    return [e for e in candidates if e.user.email.strip().lower() == e.user.workhub_access.email.strip().lower()
        == e.invitation.email.strip().lower() and e.invitation.email.lower().endswith('@'+settings.WORKHUB_EMAIL_DOMAIN)]


def request_auto_sync():
    now = timezone.now()
    SchedulerLease.objects.get_or_create(key='hiworks-directory',defaults={'expires_at':now})
    SchedulerLease.objects.filter(key='hiworks-directory',token='').update(expires_at=now)


def sync_automatic_links():
    if not settings.HIWORKS_DIRECTORY_TOKEN or not settings.HIWORKS_NOTIFY_TOKEN: return
    candidates = eligible_auto_employees()
    configured = set(HiworksLink.objects.filter(auto_configured=True).values_list('employee_id',flat=True))
    pending = [e for e in candidates if e.pk not in configured]
    if not pending: return
    # A previously reviewed company-mailbox connection can be reused without another API call.
    reused = 0
    for employee in list(pending):
        with transaction.atomic():
            current = Employee.objects.select_for_update().get(pk=employee.pk)
            link = HiworksLink.objects.select_for_update().filter(employee=current).first()
            email = employee.invitation.email.strip().lower()
            if link and not link.auto_configured and link.remote_id and link.email.lower() == email and link.user_id.lower() == email.split('@',1)[0]:
                if not any(e.pk == current.pk for e in eligible_auto_employees()): continue
                link.auto_configured=True;link.notifications_enabled=True
                link.save(update_fields=['auto_configured','notifications_enabled'])
                pending.remove(employee);reused += 1
    if reused:
        HiworksSyncStatus.objects.update_or_create(key='directory',defaults={'checked_at':timezone.now(),'status':'ok','message':f'{reused}명의 기존 연결을 자동 연동으로 전환했습니다.'})
    if not pending: return
    now = timezone.now(); token = secrets.token_hex(16)
    SchedulerLease.objects.get_or_create(key='hiworks-directory',defaults={'expires_at':now})
    if not SchedulerLease.objects.filter(key='hiworks-directory',expires_at__lte=now).update(token=token,expires_at=now+timedelta(minutes=3),heartbeat_at=now): return
    retry_minutes = 15
    try:
        from accounts.hiworks_access import fresh_cache
        cached=fresh_cache() if settings.WORKHUB_REQUIRE_HIWORKS else None
        rows = {row['email']:row for row in (cached.rows if cached else directory_rows())}
        linked = missing = conflicts = 0
        for employee in pending:
            row = rows.get(employee.invitation.email.strip().lower())
            if not row:
                missing += 1; continue
            try:
                with transaction.atomic():
                    current = Employee.objects.select_for_update().get(pk=employee.pk)
                    # Recheck verified onboarding and identity immediately before applying.
                    if not any(e.pk == current.pk for e in eligible_auto_employees()): continue
                    link = HiworksLink.objects.select_for_update().filter(employee=current).first()
                    if link and link.auto_configured: continue
                    if (link and (link.remote_id != row['remote_id'] or link.email.lower() != row['email'])) or HiworksLink.objects.filter(remote_id=row['remote_id']).exclude(employee=current).exists():
                        conflicts += 1; continue
                    if not link: link = HiworksLink(employee=current)
                    link.remote_id=row['remote_id'];link.user_id=row['user_id'];link.email=row['email']
                    link.notifications_enabled=True;link.auto_configured=True;link.save()
                    linked += 1
            except IntegrityError:
                conflicts += 1
        HiworksSyncStatus.objects.update_or_create(key='directory',defaults={'checked_at':now,
            'status':'attention' if missing or conflicts else 'ok',
            'message':f'{linked}명 자동 연결 완료 · 하이웍스 계정 확인 필요 {missing}명 · 연결 충돌 {conflicts}명'})
    except ValidationError as exc:
        retry_minutes = 5
        HiworksSyncStatus.objects.update_or_create(key='directory',defaults={'checked_at':now,'status':'error','message':exc.messages[0][:240]})
    finally:
        SchedulerLease.objects.filter(key='hiworks-directory',token=token).update(token='',expires_at=timezone.now()+timedelta(minutes=retry_minutes))


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
