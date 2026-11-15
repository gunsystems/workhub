"""Delegated file transfer. Provider URLs and credentials never enter HTML/logs."""
from urllib.parse import urlsplit, quote
import requests
from .sharepoint_probe import SharePointProbeError
from .shared_document_http import graph_write

GRAPH = 'https://graph.microsoft.com/v1.0'
CHUNK = 5 * 1024 * 1024  # A multiple of Graph's 320 KiB fragment size.


def transfer_url(value, config):
    try:
        p = urlsplit(value)
        return (p.scheme == 'https' and not p.username and not p.password and p.port in (None, 443)
                and not p.fragment and (p.hostname == urlsplit(config.site_url).hostname
                or p.hostname.endswith('.1drv.com')))
    except (ValueError, TypeError, AttributeError):
        return False


def failure(status):
    return SharePointProbeError({401: '문서 계정을 다시 연결해 주세요.',
        403: '이 작업을 할 문서 권한이 없습니다. 관리자에게 문의하세요.',
        404: '파일이나 폴더가 이동되거나 삭제되었습니다.',
        409: '같은 이름의 파일이 있습니다. 파일 이름을 바꿔 다시 등록하세요.',
        412: '다른 사람이 파일을 변경했습니다. 목록을 새로고침한 뒤 다시 진행하세요.',
        423: '다른 작업에서 파일을 사용 중입니다. 잠시 후 다시 진행하세요.',
        429: '요청이 많습니다. 잠시 후 다시 진행하세요.'}.get(status,
        '처리 결과를 확인하지 못했습니다. 다시 실행하기 전에 문서 목록을 확인하세요.'))


def upload(client, drive, folder, file, existing=None):
    from .shared_documents import segment
    client.library(drive)
    if not existing and folder and 'folder' not in client.item(drive, folder):
        raise SharePointProbeError('등록할 폴더를 확인해 주세요.')
    name = existing['name'] if existing else file.name
    if existing:
        if not existing.get('eTag'):
            raise SharePointProbeError('파일 변경 상태를 확인하지 못했습니다.')
        resource = f'{GRAPH}/drives/{segment(drive)}/items/{segment(existing["id"])}/createUploadSession'
    else:
        resource = f'{GRAPH}/drives/{segment(drive)}/' + (f'items/{segment(folder)}' if folder else 'root')
        resource += ':/' + quote(name, safe='') + ':/createUploadSession'
    session = graph_write(resource, client.token, 'POST',
                          {'item': {'name': name, '@microsoft.graph.conflictBehavior': 'fail'}},
                          etag=existing['eTag'] if existing else None, label='파일')
    url = session.get('uploadUrl')
    if not transfer_url(url, client.config):
        raise SharePointProbeError('파일 등록 연결을 확인하지 못했습니다.')
    offset = 0
    try:
        while offset < file.size:
            chunk = file.read(min(CHUNK, file.size - offset))
            if not chunk:
                raise ValueError
            end = offset + len(chunk)
            with requests.put(url, data=chunk, headers={'Content-Length': str(len(chunk)),
                    'Content-Range': f'bytes {offset}-{end - 1}/{file.size}',
                    'Content-Type': 'application/octet-stream'}, timeout=(10, 45), allow_redirects=False) as response:
                if response.status_code not in (200, 201, 202):
                    raise failure(response.status_code)
                if end < file.size and response.status_code != 202:
                    raise ValueError
                if end == file.size:
                    if response.status_code not in (200, 201):
                        raise ValueError
                    result = response.json()
                    client.validate_item(result, drive)
                    if ('file' not in result or result.get('name') != name or result.get('size') != file.size
                            or (existing and result.get('id') != existing['id'])):
                        raise ValueError
                    return result
            offset = end
    except (requests.RequestException, ValueError, TypeError, KeyError):
        raise SharePointProbeError('등록 결과를 확인하지 못했습니다. 문서 목록을 확인한 뒤 다시 진행하세요.') from None


def download(client, drive, row):
    from .shared_documents import segment
    url = f'{GRAPH}/drives/{segment(drive)}/items/{segment(row["id"])}/content'
    response = None
    try:
        response = requests.get(url, headers={'Authorization': 'Bearer ' + client.token,
            'If-Match': row.get('eTag', '')}, timeout=(10, 30), stream=True, allow_redirects=False)
        for _ in range(4):
            if response.status_code == 200:
                return response
            if response.status_code not in (301, 302, 303, 307, 308):
                raise failure(response.status_code)
            location = response.headers.get('Location', '')
            response.close()
            if not transfer_url(location, client.config):
                raise SharePointProbeError('다운로드 주소를 확인하지 못했습니다.')
            # The signed URL is obtained only from Graph; never forward the bearer token.
            response = requests.get(location, timeout=(10, 30), stream=True, allow_redirects=False)
        raise SharePointProbeError('다운로드 연결을 확인하지 못했습니다.')
    except (requests.RequestException, SharePointProbeError):
        if response is not None:
            response.close()
        raise SharePointProbeError('파일을 내려받지 못했습니다. 문서 연결과 열람 권한을 확인하세요.') from None
