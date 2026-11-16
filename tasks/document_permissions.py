"""Administrator-only live SharePoint permissions with explicit impact preview."""
import base64
import hashlib
import secrets
import uuid
from datetime import timedelta
from functools import wraps
from urllib.parse import urlencode
from cryptography.fernet import InvalidToken
from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import IntegrityError, transaction
from django.db.models import Value
from django.db.models.functions import Coalesce, NullIf
from django.shortcuts import render, redirect
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_POST, require_http_methods
from .audit import write
from .models import Employee, DocumentPermissionConnection, DocumentPermissionLease
from .shared_documents import cipher, token_request, required_scope, GRAPH, folder_location
from .sharepoint_probe import ProbeConfig, SharePointProbeError, _json_request
from .document_permission_api import PermissionClient, digest


def admin_only(view):
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        if not request.user.is_active or not request.user.is_superuser:
            raise PermissionDenied
        return view(request, *args, **kwargs)
    return login_required(wrapped)


def callback_url():
    from django.conf import settings
    return settings.WORKHUB_BASE_URL.rstrip('/') + '/work/shared-documents/permissions/callback/'


@admin_only
@never_cache
@require_GET
def setup(request):
    return render(request, 'tasks/document_permission_setup.html', {
        'permission_connection': DocumentPermissionConnection.objects.filter(user=request.user).first(),
        'callback_url': callback_url()})


@admin_only
@never_cache
@require_POST
def connect(request):
    try:
        config = ProbeConfig.from_settings()
    except SharePointProbeError as exc:
        messages.error(request, str(exc))
        return redirect('document_permission_setup')
    verifier, state = secrets.token_urlsafe(64), secrets.token_urlsafe(32)
    request.session['document_permission_oauth'] = {'user': request.user.pk, 'state': state,
        'verifier': verifier, 'at': timezone.now().timestamp()}
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip('=')
    return redirect(f'https://login.microsoftonline.com/{config.tenant_id}/oauth2/v2.0/authorize?' + urlencode({
        'client_id': config.client_id, 'redirect_uri': callback_url(), 'response_type': 'code',
        'response_mode': 'query', 'scope': 'offline_access ' + PermissionClient.scope(config),
        'state': state, 'code_challenge': challenge, 'code_challenge_method': 'S256', 'prompt': 'select_account'}))


@admin_only
@never_cache
@require_GET
def callback(request):
    state = request.session.pop('document_permission_oauth', {})
    try:
        config = ProbeConfig.from_settings()
        if (state.get('user') != request.user.pk or not isinstance(state.get('state'), str)
                or not secrets.compare_digest(state['state'], request.GET.get('state', ''))
                or not 0 <= timezone.now().timestamp() - state.get('at', 0) < 600
                or request.GET.get('error') or not request.GET.get('code')):
            raise SharePointProbeError('관리자 연결 요청이 만료되었습니다.')
        result = token_request(config, {'grant_type': 'authorization_code', 'code': request.GET['code'],
            'code_verifier': state['verifier'], 'redirect_uri': callback_url()})
        if not result.get('refresh_token') or not result.get('access_token'):
            raise SharePointProbeError('관리자 연결 권한을 확인하지 못했습니다.')
        # Obtain a Graph token from the same refresh grant and verify identity via /me,
        # rather than trusting an unverified ID token or an email from the request.
        graph = token_request(config, {'grant_type': 'refresh_token', 'refresh_token': result['refresh_token'],
                                       'scope': 'offline_access User.Read Sites.Selected'})
        required_scope(graph)
        person = _json_request(GRAPH + '/me?$select=id,mail,userPrincipalName', token=graph['access_token'])
        email = str(person.get('mail') or person.get('userPrincipalName') or '').strip().lower()
        if not email.endswith('@gunsystems.co.kr'):
            raise SharePointProbeError('회사 Microsoft 계정으로 연결해 주세요.')
        subject = uuid.UUID(str(person.get('id', '')))
        # Verify SharePoint token/user and web ManagePermissions before replacing a connection.
        checker = object.__new__(PermissionClient)
        checker.config, checker.token = config, result['access_token']
        me = checker.call('currentuser?$select=Id,Email,LoginName,IsSiteAdmin')
        known = {str(me.get('Email', '')).lower(), str(me.get('LoginName', '')).rsplit('|', 1)[-1].lower()}
        if email not in known:
            raise SharePointProbeError('회사 계정 확인에 실패했습니다.')
        effective = checker.call('effectivebasepermissions')
        if not int(effective.get('Low', 0)) & (1 << 25):
            raise SharePointProbeError('이 사이트의 권한을 관리할 수 있는 회사 계정이 필요합니다.')
        DocumentPermissionConnection.objects.update_or_create(user=request.user, defaults={
            'tenant': config.tenant_id, 'client': config.client_id, 'subject': subject, 'email': email,
            'site_url': config.site_url, 'connected_at': timezone.now(),
            'encrypted_refresh_token': cipher().encrypt(graph.get('refresh_token', result['refresh_token']).encode()).decode()})
        write('update', 'DocumentPermissionConnection', request.user.pk, '문서 권한 관리자 연결',
              after={'연결 계정': email}, actor=request.user)
        messages.success(request, '관리자 문서 권한 계정을 연결했습니다.')
    except (SharePointProbeError, ValueError, TypeError, KeyError):
        messages.error(request, '관리자 권한 연결을 완료하지 못했습니다. 전용 사이트 접근 설정과 권한을 확인해 주세요.')
    response = redirect('document_permission_setup')
    response['Referrer-Policy'] = 'no-referrer'
    return response


def employees():
    return Employee.objects.filter(is_active=True).annotate(email=Coalesce(
        NullIf('user__email', Value('')), NullIf('invitation__email', Value('')),
        'hiworkslink__email', Value(''))).filter(
        email__iendswith='@gunsystems.co.kr').select_related('department').order_by('department__name', 'name')


def safe_snapshot(snapshot):
    return {'권한 분리': snapshot['unique'], '허용 대상': [
        {'대상': r['name'], '이메일': r['email'], '권한': [{1:'제한된 접근',2:'보기',3:'편집',5:'관리',6:'편집'}.get(kind, '사용자 정의') for _, kind in r['roles']],
         '관리자 보존': r['protected']} for r in snapshot['rows'] if not r['limited']]}


