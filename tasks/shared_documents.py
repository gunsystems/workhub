"""Read-only SharePoint browsing through the employee's delegated identity.

The server application token is intentionally never used in these views.
"""
import base64
import hashlib
import re
import secrets
import uuid
from urllib.parse import quote, urlencode, unquote, urlsplit

from cryptography.fernet import Fernet, InvalidToken
from django import forms
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST, require_GET, require_http_methods

from .models import DocumentConnection, SharedDocument, SharedDocumentLink
from .sharepoint_probe import ProbeConfig, SharePointProbeError, _json_request, site_libraries
from .documents import parent_for, can_add
from .permissions import visible_tasks, visible_projects

SCOPES = 'offline_access User.Read Sites.Selected'
GRAPH = 'https://graph.microsoft.com/v1.0'


def callback_url():
    return settings.WORKHUB_BASE_URL.rstrip('/') + '/work/shared-documents/callback/'


def cipher():
    key = base64.urlsafe_b64encode(hashlib.sha256((settings.SECRET_KEY + ':document-oauth-v1').encode()).digest())
    return Fernet(key)


def token_request(config, data):
    return _json_request(f'https://login.microsoftonline.com/{config.tenant_id}/oauth2/v2.0/token',
        data={'client_id': config.client_id, 'client_secret': config.client_secret, **data})


def required_scope(result):
    scopes = {s.lower().removeprefix('https://graph.microsoft.com/') for s in str(result.get('scope', '')).split()}
    if 'sites.selected' not in scopes or 'user.read' not in scopes or not result.get('access_token'):
        raise SharePointProbeError('문서 읽기 연결 권한을 확인해 주세요.')


def safe_web_url(value, config):
    try:
        parsed, root = urlsplit(value), urlsplit(config.site_url)
        path = unquote(parsed.path)
        return (parsed.scheme == 'https' and parsed.netloc == root.netloc and
                path.casefold().startswith(root.path.casefold() + '/') and
                not any(p in ('.', '..') for p in path.split('/')) and '\\' not in path and
                '%' not in path and not any(ord(c) < 32 for c in value))
    except (ValueError, TypeError):
        return False


def segment(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9!_.-]{1,200}', value) or value in ('.', '..'):
        raise Http404
    return quote(value, safe='')


class UserDocuments:
    def __init__(self, user):
        if not user.is_authenticated or not user.is_active:
            raise PermissionDenied
        self.config = ProbeConfig.from_settings()
        connection = DocumentConnection.objects.filter(user=user).first()
        if (not connection or str(connection.tenant) != self.config.tenant_id or
                str(connection.client) != self.config.client_id or
                connection.email.casefold() != user.email.strip().casefold()):
            raise SharePointProbeError('회사 Microsoft 계정을 문서함에 연결해 주세요.')
        try:
            refresh = cipher().decrypt(connection.encrypted_refresh_token.encode()).decode()
        except (InvalidToken, ValueError):
            raise SharePointProbeError('문서 계정을 다시 연결해 주세요.') from None
        auth = token_request(self.config, {'grant_type': 'refresh_token', 'refresh_token': refresh, 'scope': SCOPES})
        required_scope(auth)
        self.token = auth['access_token']
        if auth.get('refresh_token'):
            DocumentConnection.objects.filter(pk=connection.pk).update(
                encrypted_refresh_token=cipher().encrypt(auth['refresh_token'].encode()).decode())
        self.libraries = site_libraries(self.config, self.token)

    def library(self, drive):
        for library in self.libraries:
            if library['id'] == drive:
                return library
        # No title, existence or cross-site item information is disclosed.
        raise Http404

    def item(self, drive, item):
        self.library(drive)
        result = _json_request(f'{GRAPH}/drives/{segment(drive)}/items/{segment(item)}'
            '?$select=id,name,webUrl,file,folder,size,lastModifiedDateTime,parentReference', token=self.token)
        self.validate_item(result, drive)
        return result

    def validate_item(self, row, drive):
        if (not isinstance(row, dict) or not isinstance(row.get('name'), str) or
                not isinstance(row.get('id'), str) or not safe_web_url(row.get('webUrl'), self.config) or
                not isinstance(row.get('parentReference'), dict) or row['parentReference'].get('driveId') != drive):
            raise SharePointProbeError('문서 정보를 확인하지 못했습니다.')

    def children(self, drive, folder=''):
        self.library(drive)
        if folder:
            if 'folder' not in self.item(drive, folder):
                raise Http404
        resource = f'{GRAPH}/drives/{segment(drive)}/' + (f'items/{segment(folder)}' if folder else 'root') + '/children'
        url = resource + '?$select=id,name,webUrl,file,folder,size,lastModifiedDateTime,parentReference&$top=100'
        rows, seen = [], set()
        for _ in range(5):
            if url in seen:
                raise SharePointProbeError('목록 응답이 반복되어 조회를 중단했습니다.')
            seen.add(url)
            page = _json_request(url, token=self.token)
            values = page.get('value')
            if not isinstance(values, list):
                raise SharePointProbeError('문서 목록을 확인하지 못했습니다.')
            for row in values:
                self.validate_item(row, drive)
                if 'file' in row or 'folder' in row:
                    rows.append(row)
            url = page.get('@odata.nextLink')
            if not url:
                return sorted(rows, key=lambda row: ('folder' not in row, row['name'].casefold()))
            try:
                valid = isinstance(url, str) and urlsplit(url)._replace(query='').geturl() == resource
            except ValueError:
                valid = False
            if not valid:
                raise SharePointProbeError('다음 문서 목록의 주소를 확인하지 못했습니다.')
        raise SharePointProbeError('폴더의 항목이 많습니다. SharePoint에서 폴더를 나눠 주세요.')


@login_required
@require_POST
@never_cache
def connect(request):
    try:
        config = ProbeConfig.from_settings()
    except SharePointProbeError as exc:
        messages.error(request, str(exc))
        return redirect('shared_documents')
    verifier, state = secrets.token_urlsafe(64), secrets.token_urlsafe(32)
    request.session['document_oauth'] = {'user': request.user.pk, 'state': state, 'verifier': verifier, 'at': timezone.now().timestamp()}
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip('=')
    return redirect(f'https://login.microsoftonline.com/{config.tenant_id}/oauth2/v2.0/authorize?' + urlencode({
        'client_id': config.client_id, 'redirect_uri': callback_url(), 'response_type': 'code', 'response_mode': 'query',
        'scope': SCOPES, 'state': state, 'code_challenge': challenge, 'code_challenge_method': 'S256', 'prompt': 'select_account'}))


@login_required
@require_GET
@never_cache
def callback(request):
    state = request.session.pop('document_oauth', {})
    try:
        config = ProbeConfig.from_settings()
        if (state.get('user') != request.user.pk or not isinstance(state.get('state'), str) or
                not secrets.compare_digest(state['state'], request.GET.get('state', '')) or
                not 0 <= timezone.now().timestamp() - state.get('at', 0) < 600 or
                not request.GET.get('code') or request.GET.get('error')):
            raise SharePointProbeError('연결 요청이 만료되었습니다. 문서 계정 연결을 다시 눌러 주세요.')
        result = token_request(config, {'grant_type': 'authorization_code', 'code': request.GET['code'],
            'code_verifier': state['verifier'], 'redirect_uri': callback_url()})
        required_scope(result)
        if not result.get('refresh_token'):
            raise SharePointProbeError('연결을 유지할 수 없습니다. 문서 계정을 다시 연결해 주세요.')
        person = _json_request(GRAPH + '/me?$select=id,mail,userPrincipalName', token=result['access_token'])
        email = request.user.email.strip().lower()
        if not email or email not in {str(person.get(k) or '').strip().lower() for k in ('mail', 'userPrincipalName')}:
            raise SharePointProbeError('Workhub와 같은 회사 이메일의 Microsoft 계정으로 연결해 주세요.')
        subject = uuid.UUID(str(person.get('id') or ''))
        # Prove selected-site access before saving any connection.
        site_libraries(config, result['access_token'])
        DocumentConnection.objects.update_or_create(user=request.user, defaults={
            'tenant': config.tenant_id, 'client': config.client_id, 'subject': subject, 'email': email,
            'encrypted_refresh_token': cipher().encrypt(result['refresh_token'].encode()).decode(), 'connected_at': timezone.now()})
        messages.success(request, '회사 문서함을 연결했습니다.')
    except (SharePointProbeError, ValueError, KeyError, TypeError):
        # No code, token, provider payload, email or exception details in logs.
        messages.error(request, '문서 연결을 완료하지 못했습니다. 같은 회사 이메일과 문서함 접근 권한을 확인하고 다시 연결해 주세요.')
    response = redirect('shared_documents')
    response['Referrer-Policy'] = 'no-referrer'
    return response


@login_required
@require_POST
@never_cache
def disconnect(request):
    DocumentConnection.objects.filter(user=request.user).delete()
    messages.success(request, 'Workhub의 문서 계정 연결을 해제했습니다.')
    return redirect('shared_documents')


def parent_context(user, params):
    kind, pk = params.get('kind', ''), params.get('parent', '')
    if not kind and not pk:
        return '', None
    if kind not in ('task', 'project') or not str(pk).isdigit():
        raise Http404
    return kind, parent_for(user, kind, pk)


@login_required
@require_GET
@never_cache
def listing(request):
    kind, parent = parent_context(request.user, request.GET)
    context = {'connection': DocumentConnection.objects.filter(user=request.user).first(),
               'kind': kind, 'parent': parent, 'q': request.GET.get('q', '')[:200], 'callback_url': callback_url()}
    try:
        client = UserDocuments(request.user)
        context['libraries'] = client.libraries
        drive, folder = request.GET.get('drive', ''), request.GET.get('folder', '')
        context.update(drive=drive, folder=folder)
        if parent:
            links = SharedDocumentLink.objects.filter(**{kind: parent}, document__site_url=client.config.site_url).select_related('document')
            documents = []
            if links.count() > 50:
                raise SharePointProbeError('연결된 문서가 많습니다. 관리자에게 문의해 주세요.')
            for link in links:
                try:
                    row = client.item(link.document.drive_id, link.document.item_id)
                except (SharePointProbeError, Http404):
                    continue  # No cached name or title for revoked files.
                if 'file' in row:
                    documents.append({'reference': link.document, 'name': row['name']})
            context['linked_documents'] = documents
        if drive:
            context['library'] = client.library(drive)
            context['rows'] = [row for row in client.children(drive, folder) if context['q'].casefold() in row['name'].casefold()]
    except SharePointProbeError as exc:
        context['error'] = str(exc)
    return render(request, 'tasks/shared_documents.html', context)


@login_required
@require_GET
@never_cache
def open_document(request, drive, item):
    try:
        row = UserDocuments(request.user).item(drive, item)
        if 'file' not in row:
            raise Http404
        response = redirect(row['webUrl'])
        response['Referrer-Policy'] = 'no-referrer'
        return response
    except SharePointProbeError:
        messages.error(request, '문서를 열 수 없습니다. 연결 상태와 열람 권한을 확인하세요.')
        return redirect('shared_documents')


class LinkForm(forms.Form):
    kind = forms.ChoiceField(label='연결 대상', choices=[('task', '업무'), ('project', '프로젝트')])
    task = forms.ModelChoiceField(label='업무', queryset=SharedDocument.objects.none(), required=False)
    project = forms.ModelChoiceField(label='프로젝트', queryset=SharedDocument.objects.none(), required=False)

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user
        self.fields['task'].queryset = visible_tasks(user).filter(is_deleted=False)
        self.fields['project'].queryset = visible_projects(user)

    def clean(self):
        data = super().clean()
        kind = data.get('kind')
        parent = data.get(kind) if kind else None
        if not parent:
            raise forms.ValidationError('연결할 업무 또는 프로젝트를 선택하세요.')
        if not can_add(self.user, kind, parent):
            raise forms.ValidationError('이 업무에 자료를 연결할 권한이 없습니다.')
        data['parent'] = parent
        return data


@login_required
@never_cache
@require_http_methods(['GET', 'POST'])
def link_document(request, drive, item):
    try:
        client = UserDocuments(request.user)
        row = client.item(drive, item)
        if 'file' not in row:
            raise Http404
    except SharePointProbeError:
        messages.error(request, '문서를 연결할 수 없습니다. 문서함 연결과 열람 권한을 확인하세요.')
        return redirect('shared_documents')
    initial = {k: request.GET.get(k) for k in ('kind', 'task', 'project') if request.GET.get(k)}
    form = LinkForm(request.POST if request.method == 'POST' else None, user=request.user, initial=initial)
    if request.method == 'POST' and form.is_valid():
        kind, parent = form.cleaned_data['kind'], form.cleaned_data['parent']
        with transaction.atomic():
            parent = type(parent).objects.select_for_update().get(pk=parent.pk)
            parent_for(request.user, kind, parent.pk)
            if not can_add(request.user, kind, parent):
                raise PermissionDenied
            document, _ = SharedDocument.objects.get_or_create(site_url=client.config.site_url, drive_id=drive, item_id=item)
            existing = SharedDocumentLink.objects.filter(document=document, **{kind: parent}).first()
            if not existing and SharedDocumentLink.objects.filter(**{kind: parent}).count() >= 50:
                form.add_error(None, '업무별 공동 문서는 최대 50개까지 연결할 수 있습니다.')
            else:
                link, created = SharedDocumentLink.objects.get_or_create(document=document, **{kind: parent}, defaults={'creator': request.user})
                if created:
                    from .audit import write
                    write('create', 'SharedDocumentLink', link.pk, '공동 문서 연결',
                          after={'연결 종류': kind, '연결 번호': parent.pk}, actor=request.user)
                messages.success(request, '공동 문서를 연결했습니다.' if created else '이미 연결된 문서입니다.')
                return redirect(reverse('shared_documents') + '?' + urlencode({'kind': kind, 'parent': parent.pk}))
    return render(request, 'tasks/shared_document_link.html', {'form': form, 'document': row, 'drive': drive, 'item': item})
