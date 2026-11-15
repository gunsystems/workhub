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


class AccessForm(forms.Form):
    reason = forms.CharField(label='변경 사유', max_length=500, widget=forms.Textarea(attrs={'rows': 3}))

    def __init__(self, *args, employees, snapshot, protected_email='', **kwargs):
        super().__init__(*args, **kwargs)
        rows = snapshot['rows']
        self.employees = list(employees)
        for employee in self.employees:
            row = next((r for r in rows if r['email'] == employee.email.casefold()), None)
            # Site administrators can have access without a direct item assignment.
            protected = employee.email.casefold() == protected_email.casefold() or bool(row and row['protected'])
            value = 'none'
            if row and not row['protected'] and not row['limited']:
                value = 'write' if any(kind in (3, 6) for _, kind in row['roles']) else 'read'
            self.fields['employee_' + str(employee.pk)] = forms.ChoiceField(
                label=f'{employee.department} · {employee.name} · {employee.email}',
                choices=[('owner', '관리자 접근 유지')] if protected else [('none', '허용하지 않음'), ('read', '보기'), ('write', '편집')],
                initial='owner' if protected else value, disabled=protected)

    def desired(self):
        result = []
        for employee in self.employees:
            role = self.cleaned_data['employee_' + str(employee.pk)]
            if role in ('read', 'write'):
                result.append({'employee': employee.pk, 'name': employee.name,
                               'email': employee.email.casefold(), 'role': role})
        return result


def employees():
    return Employee.objects.filter(is_active=True).annotate(email=Coalesce(
        NullIf('user__email', Value('')), 'invitation__email', Value(''))).filter(
        email__iendswith='@gunsystems.co.kr').select_related('department').order_by('department__name', 'name')


def safe_snapshot(snapshot):
    return {'권한 분리': snapshot['unique'], '허용 대상': [
        {'대상': r['name'], '이메일': r['email'], '권한': [{1:'제한된 접근',2:'보기',3:'편집',5:'관리',6:'편집'}.get(kind, '사용자 정의') for _, kind in r['roles']],
         '관리자 보존': r['protected']} for r in snapshot['rows'] if not r['limited']]}


@admin_only
@never_cache
@require_http_methods(['GET', 'POST'])
def manage(request, drive, item):
    context = {'drive': drive, 'item': item, 'cancel_url': folder_location(drive)}
    try:
        client = PermissionClient(request.user)
        context['target'] = client.target(drive, item)
        snapshot = client.snapshot()
        context['snapshot'] = snapshot
        context['protected'] = [r for r in snapshot['rows'] if r['protected']]
        staff = list(employees())
        if len(staff) > 30 or len({s.email.casefold() for s in staff}) != len(staff):
            raise SharePointProbeError('직원 이메일 중복 또는 관리 대상 수를 확인해 주세요.')
        context['form'] = form = AccessForm(request.POST or None, employees=staff, snapshot=snapshot,
                                          protected_email=client.connection.email)
        if request.method == 'POST' and form.is_valid():
            nonce = secrets.token_urlsafe(24)
            data = {'user': request.user.pk, 'drive': drive, 'item': item, 'at': timezone.now().timestamp(),
                'connection': str(client.connection.connected_at), 'site': client.config.site_url,
                'snapshot': snapshot, 'desired': form.desired(), 'reason': form.cleaned_data['reason'],
                'name': context['target']['name']}
            request.session['document_permission_preview'] = {nonce: data}
            context.update(preview=data, nonce=nonce)
    except (SharePointProbeError, InvalidToken) as exc:
        context['error'] = str(exc) if isinstance(exc, SharePointProbeError) else '관리자 문서 권한 계정을 다시 연결해 주세요.'
    return render(request, 'tasks/document_permissions.html', context)


@admin_only
@never_cache
@require_POST
def apply(request, drive, item):
    preview = request.session.pop('document_permission_preview', {}).get(request.POST.get('nonce', ''), {})
    before, client, lease = None, None, None
    error = None
    try:
        if (preview.get('user') != request.user.pk or preview.get('drive') != drive or preview.get('item') != item
                or not 0 <= timezone.now().timestamp() - preview.get('at', 0) < 600
                or request.POST.get('confirmed') != 'yes'):
            raise SharePointProbeError('확인 요청이 만료되었습니다. 권한을 다시 검토해 주세요.')
        client = PermissionClient(request.user)
        client.target(drive, item)
        if preview['connection'] != str(client.connection.connected_at) or preview['site'] != client.config.site_url:
            raise SharePointProbeError('관리자 연결이 변경되었습니다. 다시 검토해 주세요.')
        current_staff = {s.pk: s for s in employees()}
        for row in preview['desired']:
            staff = current_staff.get(row['employee'])
            if not staff or staff.email.casefold() != row['email'] or row['role'] not in ('read', 'write'):
                raise SharePointProbeError('직원 정보가 변경되었습니다. 권한을 다시 검토해 주세요.')
        key = digest([client.config.site_url, drive, item])
        try:
            with transaction.atomic():
                DocumentPermissionLease.objects.filter(target=key, expires_at__lt=timezone.now()).delete()
                lease = DocumentPermissionLease.objects.create(target=key, expires_at=timezone.now() + timedelta(minutes=15))
        except IntegrityError:
            raise SharePointProbeError('다른 권한 변경이 진행 중입니다. 잠시 후 현재 권한을 다시 조회하세요.') from None
        before = preview['snapshot']
        write('permission_started', 'SharePointPermission', item, '공동 문서 권한 변경 시작 · ' + preview['name'],
              before=safe_snapshot(before), after={'요청한 직원': preview['desired'], '사유': preview['reason']}, actor=request.user)
        def observe(step, actual):
            if not DocumentPermissionLease.objects.filter(pk=lease.pk, expires_at__gt=timezone.now()).update(
                    expires_at=timezone.now() + timedelta(minutes=15)):
                raise SharePointProbeError('권한 변경 잠금이 만료되었습니다. 현재 권한을 다시 확인하세요.')
            write('update', 'SharePointPermission', item, '공동 문서 권한 변경 단계 · ' + preview['name'],
                  after={'처리 단계': step, **safe_snapshot(actual)}, actor=request.user)
        after = client.apply(before, preview['desired'], observe)
        write('update', 'SharePointPermission', item, '공동 문서 권한 변경 · ' + preview['name'],
            before=safe_snapshot(before), after={**safe_snapshot(after), '사유': preview['reason'],
            '문서함': drive, 'Microsoft 처리 계정': client.connection.email}, actor=request.user)
        messages.success(request, '실제 문서 권한을 변경하고 결과를 확인했습니다.')
    except IntegrityError:
        error = '처리 기록을 저장하지 못했습니다. 현재 문서 권한을 다시 확인하세요.'
    except (SharePointProbeError, InvalidToken, KeyError, TypeError) as exc:
        error = str(exc) if isinstance(exc, SharePointProbeError) else '권한 처리 결과를 확인하지 못했습니다.'
        if before is not None:
            write('permission_attention', 'SharePointPermission', item, '공동 문서 권한 결과 확인 필요 · ' + preview.get('name', ''),
                  before=safe_snapshot(before), after={'처리 결과': '일부 변경이 적용되었을 수 있습니다. 실제 권한 재조회 필요'}, actor=request.user)
    finally:
        if lease is not None:
            DocumentPermissionLease.objects.filter(pk=lease.pk).delete()
    if error:
        messages.error(request, error)
    return redirect('document_permission_manage', drive=drive, item=item)
