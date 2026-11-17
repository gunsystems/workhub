"""Read-only, delegated file version metadata; no bearer/download URLs in HTML."""
import json
from urllib.parse import urlencode, urlsplit
from cryptography.fernet import InvalidToken
from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.shortcuts import render
from django.urls import reverse
from django.utils.dateparse import parse_datetime
from django.utils.timezone import is_aware
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET
from .shared_documents import UserDocuments, GRAPH, segment, cipher, folder_location
from .shared_files import parent_id
from .sharepoint_probe import SharePointProbeError, _json_request


def version_page(client, drive, item, cursor=''):
    # Fresh delegated item access on every page, including cursor continuations.
    target = client.item(drive, item)
    if 'file' not in target:
        raise Http404
    resource = f'{GRAPH}/drives/{segment(drive)}/items/{segment(item)}/versions'
    scope = [list(client.cursor_identity), drive, item, 'versions']
    url = resource + '?$top=50'
    if cursor:
        try:
            if len(cursor) > 18000:
                raise ValueError
            payload = json.loads(cipher().decrypt(cursor.encode(), ttl=900))
            if payload['scope'] != scope:
                raise ValueError
            url = payload['url']
        except (InvalidToken, ValueError, TypeError, KeyError, UnicodeError, AttributeError):
            raise SharePointProbeError('이력 조회가 만료되었습니다. 처음부터 다시 확인하세요.') from None
    def valid_url(value):
        try:
            return isinstance(value, str) and len(value) <= 8192 and urlsplit(value)._replace(query='').geturl() == resource
        except ValueError:
            return False
    if not valid_url(url):
        raise SharePointProbeError('수정 이력의 조회 위치를 확인하지 못했습니다.')
    raw = _json_request(url, token=client.token)
    values = raw.get('value')
    if not isinstance(values, list) or len(values) > 200:
        raise SharePointProbeError('수정 이력을 확인하지 못했습니다.')
    rows = []
    for value in values:
        if not isinstance(value, dict):
            raise SharePointProbeError('수정 이력을 확인하지 못했습니다.')
        try:
            segment(value.get('id'))
        except Http404:
            raise SharePointProbeError('버전 정보를 확인하지 못했습니다.') from None
        who = value.get('lastModifiedBy') or {}
        if not isinstance(who, dict):
            who = {}
        user, app = who.get('user') or {}, who.get('application') or {}
        actor = (user.get('displayName') if isinstance(user, dict) else '') or (app.get('displayName') if isinstance(app, dict) else '')
        if not isinstance(actor, str):
            actor = ''
        try:
            at = parse_datetime(value.get('lastModifiedDateTime') or '')
            if at is not None and not is_aware(at):
                at = None
        except (ValueError, TypeError):
            at = None
        size = value.get('size')
        rows.append({'version': value['id'], 'actor': actor or '수정자 정보 없음', 'at': at,
                     'size': size if type(size) is int and size >= 0 else None})
    next_url, next_cursor = raw.get('@odata.nextLink'), ''
    if next_url:
        if not valid_url(next_url) or next_url == url:
            raise SharePointProbeError('다음 수정 이력의 조회 위치를 확인하지 못했습니다.')
        next_cursor = cipher().encrypt(json.dumps({'scope': scope, 'url': next_url}).encode()).decode()
    return target, rows, next_cursor


@login_required
@never_cache
@require_GET
def history(request, drive, item):
    context = {'drive': drive, 'item': item}
    try:
        client = UserDocuments(request.user)
        target, rows, cursor = version_page(client, drive, item, request.GET.get('cursor', ''))
        context.update(target=target, rows=rows, back_url=folder_location(drive, parent_id(target)))
        if cursor:
            context['next_url'] = reverse('shared_document_history', args=[drive, item]) + '?' + urlencode({'cursor': cursor})
    except SharePointProbeError as exc:
        message = str(exc)
        context['error'] = ('수정 이력을 조회할 권한이 없거나 문서 연결이 만료되었습니다. 관리자에게 문의하세요.'
                            if any(word in message for word in ('앱', '사이트 주소', '접근 허용')) else message)
    return render(request, 'tasks/shared_document_history.html', context)
