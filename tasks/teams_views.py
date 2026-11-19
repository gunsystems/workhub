"""Self-service linking with a short-lived, single-use proof from Workhub."""
import hashlib
import io
import json
import logging
import re
import secrets
from datetime import timedelta
from pathlib import Path
from urllib.parse import urlsplit
from uuid import UUID
from zipfile import ZipFile
from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import transaction, IntegrityError
from django.db.models import F
from django.http import HttpResponse, JsonResponse
from django.shortcuts import render, redirect
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST, require_http_methods
from accounts.lifecycle import account_allowed, system_admin
from .models import TeamsConnection, TeamsDelivery
from . import teams_transport as transport

logger = logging.getLogger(__name__)


def authentication_failure_reason(error):
    """Bounded diagnostic only: never log tokens, activities or exception text."""
    if type(error) is ValueError:
        allowed = {'activity', 'disabled', 'bearer', 'token', 'algorithm', 'keys',
                   'endorsement', 'service', 'service claim', 'scope', 'conversation'}
        if error.args and error.args[0] in allowed:
            return error.args[0]
    name = type(error).__name__
    return name if name in {'InvalidAudienceError', 'InvalidIssuerError',
        'ExpiredSignatureError', 'InvalidSignatureError', 'MissingRequiredClaimError',
        'ImmatureSignatureError', 'DecodeError', 'HTTPError', 'ConnectionError',
        'Timeout', 'ReadTimeout', 'ConnectTimeout', 'SSLError'} else 'invalid'


def verified_user(user):
    access = getattr(user, 'workhub_access', None)
    return bool(account_allowed(user) and access and access.verified_at and user.email and
                access.email.casefold() == user.email.casefold())


@never_cache
@login_required
@require_http_methods(['GET', 'POST'])
def preferences(request):
    if not verified_user(request.user):
        raise PermissionDenied
    connection, _ = TeamsConnection.objects.get_or_create(user=request.user)
    code, error = '', ''
    if request.method == 'POST':
        action = request.POST.get('action')
        with transaction.atomic():
            connection = TeamsConnection.objects.select_for_update().get(pk=connection.pk)
            if action == 'disconnect':
                connection.enabled = False
                connection.conversation = connection.subject = connection.service_url = ''
                connection.connected_at = None
                connection.code_hash = connection.code_expires_at = None
                connection.generation += 1
            elif action == 'link' and transport.enabled():
                # Code stays only in this no-store response; only its digest is saved.
                code = secrets.token_hex(16)
                connection.code_hash = hashlib.sha256(code.encode()).hexdigest()
                connection.code_expires_at = timezone.now() + timedelta(minutes=10)
            elif action == 'save':
                connection.generation += 1
                connection.enabled = request.POST.get('enabled') == 'on'
                for category in ('assignment', 'pending', 'result'):
                    setattr(connection, category, request.POST.get(category) == 'on')
            else:
                error = '연결 준비 상태를 확인한 후 다시 시도해 주세요.'
            connection.save()
        if not code and not error:
            return redirect('teams_preferences')
    cfg = transport.config()
    linked = bool(cfg and connection.connected_at and connection.conversation and
                  (connection.tenant, connection.app) == cfg[:2] and connection.email.casefold() == request.user.email.casefold())
    response = render(request, 'tasks/teams_preferences.html', {'connection': connection,
        'ready': transport.enabled(), 'linked': linked, 'code': code, 'error': error,
        'is_admin': system_admin(request.user), 'teams_app': cfg[1] if cfg else ''})
    response['Referrer-Policy'] = 'same-origin'
    return response


@never_cache
@login_required
@require_http_methods(['GET'])
def administration(request):
    if not system_admin(request.user):
        raise PermissionDenied
    values = [(name, bool(getattr(settings, name, ''))) for name in (
        'TEAMS_NOTIFICATION_TENANT_ID', 'TEAMS_NOTIFICATION_APP_ID', 'TEAMS_NOTIFICATION_CLIENT_SECRET')]
    return render(request, 'tasks/teams_administration.html', {'configuration': values,
        'ready': transport.enabled(), 'configured': bool(transport.config()),
        'endpoint': settings.WORKHUB_BASE_URL.rstrip('/') + reverse('teams_messages'),
        'connections': TeamsConnection.objects.select_related('user').order_by('user__username'),
        'deliveries': TeamsDelivery.objects.select_related('user').order_by('-pk')[:50]})


@never_cache
@login_required
@require_http_methods(['GET'])
def manifest(request):
    if not system_admin(request.user):
        raise PermissionDenied
    cfg = transport.config()
    if not cfg:
        return HttpResponse('Microsoft 알림 앱 설정을 먼저 저장하세요.', status=409)
    base = settings.WORKHUB_BASE_URL.rstrip('/')
    parsed = urlsplit(base)
    if parsed.scheme != 'https' or parsed.username or parsed.password or parsed.query or parsed.fragment:
        return HttpResponse(status=409)
    app = cfg[1]
    data = {'$schema': 'https://developer.microsoft.com/en-us/json-schemas/teams/v1.19/MicrosoftTeams.schema.json',
        'manifestVersion': '1.19', 'version': '1.0.0', 'id': app,
        'developer': {'name': 'GUNSYSTEMS', 'websiteUrl': base, 'privacyUrl': base + reverse('teams_privacy'),
                      'termsOfUseUrl': base + reverse('teams_privacy')},
        'name': {'short': 'Workhub 알림', 'full': 'Workhub 업무·전자결재 알림'},
        'description': {'short': '내 업무 배정과 결재 알림을 확인합니다.',
            'full': '본인 Workhub 계정을 연결하여 업무 배정, 결재 대기 및 처리 결과를 받습니다. 알림에서 Workhub를 열어 내용을 확인하고 처리할 수 있습니다. 수신 여부는 Workhub에서 변경할 수 있습니다.'},
        'icons': {'color': 'color.png', 'outline': 'outline.png'}, 'accentColor': '#226E58',
        'bots': [{'botId': app, 'scopes': ['personal'], 'supportsFiles': False,
                  'isNotificationOnly': False,
                  'commandLists': [{'scopes': ['personal'], 'commands': [
                      {'title': '연결', 'description': 'Workhub에서 받은 연결 코드를 함께 입력하세요.'}]}]}],
        'permissions': [], 'validDomains': [parsed.hostname]}
    buffer = io.BytesIO()
    with ZipFile(buffer, 'w') as package:
        package.writestr('manifest.json', json.dumps(data, ensure_ascii=False, indent=2))
        for name in ('color.png', 'outline.png'):
            package.write(Path(__file__).parent / 'static' / 'tasks' / 'teams' / name, name)
    response = HttpResponse(buffer.getvalue(), content_type='application/zip')
    response['Content-Disposition'] = 'attachment; filename="workhub-teams.zip"'
    return response


@require_http_methods(['GET'])
def privacy(request):
    return render(request, 'tasks/teams_privacy.html')


@csrf_exempt
@require_POST
def messages(request):
    # This is a service endpoint, not a login/session endpoint. JWT checks cannot be disabled.
    if not transport.enabled():
        return HttpResponse(status=503)
    if request.content_type != 'application/json' or len(request.body) > 32768:
        return HttpResponse(status=400)
    try:
        activity = json.loads(request.body)
        if not isinstance(activity, dict):
            raise ValueError('activity')
        tenant, app, service, conversation = transport.validate_activity(request.headers.get('Authorization'), activity)
    except Exception as error:
        logger.warning('teams_auth_rejected reason=%s', authentication_failure_reason(error))
        return HttpResponse(status=401)
    kind = activity.get('type')
    if kind == 'installationUpdate' and activity.get('action') == 'remove':
        TeamsConnection.objects.filter(tenant=tenant, app=app, conversation=conversation).update(
            enabled=False, connected_at=None, conversation='', subject='', service_url='',
            code_hash=None, code_expires_at=None, generation=F('generation') + 1)
        return JsonResponse({})
    if kind != 'message':
        return JsonResponse({})
    text = activity.get('text', '')
    match = re.fullmatch(r'연결\s+([a-f0-9]{32})', text.strip()) if isinstance(text, str) else None
    if not match:
        return JsonResponse({})
    try:
        subject = str(UUID(activity.get('from', {}).get('aadObjectId', '')))
    except (ValueError, AttributeError, TypeError):
        return HttpResponse(status=400)
    digest = hashlib.sha256(match[1].encode()).hexdigest()
    try:
        with transaction.atomic():
            connection = TeamsConnection.objects.select_for_update().select_related('user').filter(
                code_hash=digest, code_expires_at__gt=timezone.now()).first()
            if not connection or not verified_user(connection.user):
                return JsonResponse({})
            # If this user's Microsoft identity is already verified, a different company user cannot replace it.
            from .models import DocumentConnection, MicrosoftConnection
            known = DocumentConnection.objects.filter(user=connection.user, tenant=tenant).first()
            meeting = MicrosoftConnection.objects.filter(user=connection.user).first() if settings.MICROSOFT_TENANT_ID == tenant else None
            if (known and str(known.subject) != subject) or (meeting and meeting.subject.lower() != subject):
                return JsonResponse({})
            updated = TeamsConnection.objects.filter(pk=connection.pk, code_hash=digest).update(
                tenant=tenant, app=app, subject=subject, service_url=service, conversation=conversation,
                email=connection.user.email.lower(), code_hash=None, code_expires_at=None,
                connected_at=timezone.now(), generation=F('generation') + 1)
            if updated:
                from .audit import write
                write('teams_connected', 'TeamsConnection', connection.pk, 'Teams 알림 연결', actor=connection.user,
                      after={'연결': '완료'})
    except IntegrityError:
        return JsonResponse({})
    return JsonResponse({})
