"""File actions within the selected site, always using the signed-in employee."""
import secrets
from pathlib import Path
from urllib.parse import urlencode
from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core import signing
from django.http import Http404, StreamingHttpResponse
from django.shortcuts import render, redirect
from django.urls import reverse
from django.utils.http import content_disposition_header
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_http_methods
from .shared_documents import UserDocuments, FolderForm, folder_location, segment, GRAPH
from .shared_document_http import graph_write
from .sharepoint_probe import SharePointProbeError, _json_request
from .documents import validate_file, EXTENSIONS
from . import shared_file_http


class UploadForm(forms.Form):
    file = forms.FileField(label='파일 선택 · 최대 20MB', validators=[validate_file],
        widget=forms.ClearableFileInput(attrs={'data-upload-limit': '20971520',
                                               'accept': ','.join(sorted(EXTENSIONS))}))

    def clean_file(self):
        file = self.cleaned_data['file']
        name = FolderForm({'name': file.name})
        if not name.is_valid():
            raise forms.ValidationError('파일 이름에 사용할 수 없는 문자 또는 이름입니다.')
        return file


def proof(request, payload):
    keys = [key for key in request.session.keys() if key.startswith('file-confirm-')]
    for key in keys[:-4]:
        request.session.pop(key, None)
    nonce = secrets.token_urlsafe(16)
    request.session['file-confirm-' + nonce] = True
    return signing.dumps({**payload, 'user': request.user.pk, 'nonce': nonce}, salt='shared-file')


def consume(request, expected):
    try:
        data = signing.loads(request.POST.get('proof', ''), salt='shared-file', max_age=600)
        if (data.get('user') != request.user.pk or any(data.get(k) != v for k, v in expected.items())
                or not request.session.pop('file-confirm-' + data.get('nonce', ''), False)):
            raise signing.BadSignature
    except (signing.BadSignature, AttributeError, TypeError):
        raise SharePointProbeError('요청이 만료되거나 파일이 변경되었습니다. 목록에서 다시 확인해 주세요.') from None


def parent_id(row):
    return '' if row['parentReference'].get('path') in ('/drive/root:',) or row['parentReference'].get('path', '').endswith('/root:') else row['parentReference'].get('id', '')


@login_required
@never_cache
@require_http_methods(['GET', 'POST'])
def upload_file(request, drive):
    folder = (request.POST if request.method == 'POST' else request.GET).get('folder', '')
    context = {'title': '파일 업로드', 'action': 'upload', 'drive': drive, 'folder': folder,
               'cancel_url': folder_location(drive, folder)}
    expected = {'drive': drive, 'folder': folder, 'action': 'upload'}
    form = UploadForm(request.POST or None, request.FILES or None)
    context['form'] = form
    try:
        client = UserDocuments(request.user)
        context['library'] = client.library(drive)
        if folder:
            row = client.item(drive, folder)
            if 'folder' not in row:
                raise Http404
            context['location_name'] = row['name']
        if request.method == 'POST' and form.is_valid():
            consume(request, expected)
            result = shared_file_http.upload(client, drive, folder, form.cleaned_data['file'])
            from .audit import write
            write('create', 'SharePointFile', result['id'], '공동 문서 파일 업로드 · ' + result['name'],
                  after={'파일명': result['name'], '문서함': drive, '용량': result['size']}, actor=request.user)
            messages.success(request, '파일을 업로드했습니다.')
            return redirect(context['cancel_url'])
    except SharePointProbeError as exc:
        context['error'] = str(exc)
    context['proof'] = proof(request, expected)
    return render(request, 'tasks/shared_file_form.html', context)


@login_required
@never_cache
@require_http_methods(['GET', 'POST'])
def manage_file(request, drive, item, action):
    if action not in ('rename', 'move', 'delete', 'replace'):
        raise Http404
    params = request.POST if request.method == 'POST' else request.GET
    destination = params.get('to', '')
    context = {'title': {'rename': '파일 이름 변경', 'move': '파일 이동', 'delete': '파일 삭제', 'replace': '새 버전 업로드'}[action],
               'action': action, 'drive': drive, 'item': item, 'to': destination,
               'cancel_url': folder_location(drive)}
    expected = {'drive': drive, 'item': item, 'action': action, 'to': destination}
    try:
        client = UserDocuments(request.user)
        context['library'] = client.library(drive)
        row = client.item(drive, item)
        if 'file' not in row or 'root' in row:
            raise Http404
        if not row.get('eTag'):
            raise SharePointProbeError('파일 변경 상태를 확인하지 못했습니다. 다시 조회해 주세요.')
        context.update(target=row, cancel_url=folder_location(drive, parent_id(row)))
        expected['etag'] = row['eTag']
        form = (UploadForm(request.POST or None, request.FILES or None) if action == 'replace'
                else FolderForm(request.POST or None, initial={'name': row['name']}))
        if action != 'replace':
            form.fields['name'].label = '파일 이름'
        context['form'] = form
        target = None
        if action == 'move':
            if destination:
                target = client.item(drive, destination)
                if 'folder' not in target:
                    raise Http404
                context['breadcrumbs'] = client.ancestry(drive, destination)
                context['up_folder'] = parent_id(target)
            if request.method == 'GET':
                rows, cursor, _ = client.children_page(drive, destination, cursor=params.get('cursor', ''))
                context['folders'] = [value for value in rows if 'folder' in value]
                if cursor:
                    context['more_url'] = reverse('shared_file_manage', args=[drive, item, action]) + '?' + urlencode({'to': destination, 'cursor': cursor})
            context['destination_name'] = target['name'] if target else context['library']['name']
        if request.method == 'POST' and (action not in ('rename', 'replace') or form.is_valid()):
            proposed = form.cleaned_data['file'].name if action == 'replace' else form.cleaned_data.get('name', '') if action == 'rename' else row['name']
            if action in ('rename', 'replace') and Path(proposed).suffix.casefold() != Path(row['name']).suffix.casefold():
                form.add_error('file' if action == 'replace' else 'name', '파일 형식이 바뀌지 않도록 확장자는 유지해 주세요.')
            else:
                consume(request, expected)
                data = None
                if action == 'replace':
                    result = shared_file_http.upload(client, drive, parent_id(row), form.cleaned_data['file'], existing=row)
                    data = {'name': result['name'], 'size': result['size']}
                elif action == 'rename':
                    data = {'name': form.cleaned_data['name'], '@microsoft.graph.conflictBehavior': 'fail'}
                elif action == 'move':
                    if not target:
                        target = _json_request(f'{GRAPH}/drives/{segment(drive)}/root?$select=id', token=client.token)
                    if not isinstance(target.get('id'), str):
                        raise SharePointProbeError('이동할 폴더를 확인하지 못했습니다.')
                    segment(target['id'])
                    if target['id'] == row['parentReference'].get('id'):
                        raise SharePointProbeError('현재 폴더입니다. 다른 폴더를 선택하세요.')
                    data = {'parentReference': {'id': target['id']}, '@microsoft.graph.conflictBehavior': 'fail'}
                resource = f'{GRAPH}/drives/{segment(drive)}/items/{segment(item)}'
                if action != 'replace':
                    graph_write(resource, client.token, 'DELETE' if action == 'delete' else 'PATCH', data, etag=row['eTag'], label='파일')
                from .audit import write
                write('delete' if action == 'delete' else 'update', 'SharePointFile', item,
                      '공동 문서 파일 ' + {'rename': '이름 변경', 'move': '이동', 'delete': '휴지통 이동', 'replace': '새 버전 등록'}[action] + ' · ' + row['name'],
                      before={'파일명': row['name'], '문서함': drive, '폴더': parent_id(row), '용량': row.get('size'), '버전': row['eTag']},
                      after={'파일명': data.get('name', row['name']), '문서함': drive,
                             '폴더': destination if action == 'move' else parent_id(row),
                             '용량': result['size'] if action == 'replace' else row.get('size'),
                             '버전': result.get('eTag', '새 버전 등록') if action == 'replace' else row['eTag']} if data else {'휴지통': True}, actor=request.user)
                messages.success(request, {'rename': '파일 이름을 변경했습니다.', 'move': '파일을 이동했습니다.', 'delete': '파일을 휴지통으로 이동했습니다.', 'replace': '새 버전을 등록했습니다.'}[action])
                return redirect(folder_location(drive, destination) if action == 'move' else context['cancel_url'])
    except SharePointProbeError as exc:
        context['error'] = str(exc)
    context['proof'] = proof(request, expected)
    return render(request, 'tasks/shared_file_form.html', context)


@login_required
@never_cache
@require_GET
def download_file(request, drive, item):
    try:
        client = UserDocuments(request.user)
        row = client.item(drive, item)
        if 'file' not in row:
            raise Http404
        upstream = shared_file_http.download(client, drive, row)
        def chunks():
            try:
                yield from upstream.iter_content(chunk_size=65536)
            finally:
                upstream.close()
        response = StreamingHttpResponse(chunks(), content_type='application/octet-stream')
        response['Content-Disposition'] = content_disposition_header(True, row['name'])
        response['X-Content-Type-Options'] = 'nosniff'
        response['Referrer-Policy'] = 'no-referrer'
        response._resource_closers.append(upstream.close)
        return response
    except SharePointProbeError as exc:
        messages.error(request, str(exc))
        return redirect('shared_documents')
