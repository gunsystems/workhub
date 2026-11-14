"""Read-only deployment probe for a dedicated Sites.Selected application.

Not a user-facing file client: successful app access never authorizes an employee.
No upload, download, permission grant or token persistence is performed here.
"""
import json
import re
import socket
import uuid
from dataclasses import dataclass, field
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import Request, build_opener

from django.conf import settings
from .google_calendar import NoRedirect


class SharePointProbeError(Exception):
    pass


@dataclass(frozen=True)
class ProbeConfig:
    tenant_id: str
    client_id: str
    client_secret: str = field(repr=False)
    site_url: str

    def __post_init__(self):
        try:
            uuid.UUID(self.tenant_id)
            uuid.UUID(self.client_id)
        except (ValueError, TypeError, AttributeError):
            raise SharePointProbeError('문서 연결 앱의 ID 설정을 확인하세요.') from None
        try:
            parsed = urlsplit(self.site_url)
        except (ValueError, TypeError):
            raise SharePointProbeError('사이트 주소를 확인하세요.') from None
        if (parsed.scheme != 'https' or not parsed.hostname or
                not re.fullmatch(r'[a-z0-9-]+\.sharepoint\.com', parsed.netloc) or
                not re.fullmatch(r'/sites/[A-Za-z0-9_-]+', parsed.path) or
                parsed.query or parsed.fragment or not self.client_secret):
            raise SharePointProbeError('문서 연결의 비밀 설정과 사이트 주소를 확인하세요.')

    @classmethod
    def from_settings(cls):
        return cls(*(getattr(settings, name, '') for name in (
            'SHAREPOINT_TENANT_ID', 'SHAREPOINT_CLIENT_ID',
            'SHAREPOINT_CLIENT_SECRET', 'SHAREPOINT_SITE_URL')))


def _json_request(url, *, data=None, token=None):
    body = urlencode(data).encode() if data is not None else None
    headers = {'Accept': 'application/json'}
    if body is not None:
        headers['Content-Type'] = 'application/x-www-form-urlencoded'
    if token:
        headers['Authorization'] = 'Bearer ' + token
    try:
        with build_opener(NoRedirect()).open(
                Request(url, data=body, headers=headers), timeout=12) as response:
            raw = response.read(1_000_001)
        if len(raw) > 1_000_000:
            raise ValueError
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise ValueError
        return value
    except HTTPError as exc:
        # Provider payloads can contain credentials or private metadata.
        reason = {401: '앱 인증 설정을 확인하세요.',
                  403: '앱 동의와 해당 사이트의 접근 허용을 확인하세요.',
                  404: '사이트 주소와 접근 허용을 확인하세요.',
                  429: 'Microsoft 요청이 많습니다. 잠시 후 다시 확인하세요.'}
        raise SharePointProbeError(reason.get(exc.code, 'Microsoft 연결 요청에 실패했습니다.')) from None
    except (URLError, OSError, socket.timeout):
        raise SharePointProbeError('Microsoft 연결이 지연되거나 중단되었습니다.') from None
    except (ValueError, TypeError):
        raise SharePointProbeError('Microsoft 응답을 확인하지 못했습니다.') from None


def check_repository(config=None):
    """Verify configured site and library metadata, never enumerate file contents."""
    config = config or ProbeConfig.from_settings()
    auth = _json_request(
        f'https://login.microsoftonline.com/{config.tenant_id}/oauth2/v2.0/token',
        data={'client_id': config.client_id, 'client_secret': config.client_secret,
              'grant_type': 'client_credentials',
              'scope': 'https://graph.microsoft.com/.default'})
    token = auth.get('access_token')
    if not isinstance(token, str) or not token:
        raise SharePointProbeError('문서 연결 앱의 인증에 실패했습니다.')
    parsed = urlsplit(config.site_url)
    graph = 'https://graph.microsoft.com/v1.0'
    site = _json_request(f'{graph}/sites/{parsed.hostname}:{parsed.path}?$select=id,webUrl', token=token)
    site_url = site.get('webUrl')
    if not isinstance(site_url, str) or site_url.rstrip('/').casefold() != config.site_url.casefold():
        raise SharePointProbeError('설정한 사이트와 Microsoft 응답이 일치하지 않습니다.')
    site_id = site.get('id')
    if not isinstance(site_id, str) or not site_id:
        raise SharePointProbeError('사이트 식별 정보를 확인하지 못했습니다.')
    base = f'{graph}/sites/{quote(site_id, safe="")}/drives'
    url = base + '?$select=id,name,webUrl'
    libraries = []
    seen = set()
    for _ in range(20):
        if url in seen:
            raise SharePointProbeError('문서함 목록 응답이 반복되어 조회를 중단했습니다.')
        seen.add(url)
        page = _json_request(url, token=token)
        rows = page.get('value')
        if not isinstance(rows, list):
            raise SharePointProbeError('문서함 목록을 확인하지 못했습니다.')
        for row in rows:
            if not isinstance(row, dict) or not all(isinstance(row.get(k), str) and row[k] for k in ('id', 'name', 'webUrl')):
                raise SharePointProbeError('문서함 정보를 확인하지 못했습니다.')
            try:
                target = urlsplit(row['webUrl'])
            except ValueError:
                raise SharePointProbeError('문서함 주소를 확인하지 못했습니다.') from None
            if (target.scheme != 'https' or target.netloc != parsed.netloc or
                    not target.path.casefold().startswith(parsed.path.casefold() + '/')):
                raise SharePointProbeError('문서함이 지정한 사이트 범위를 벗어났습니다.')
            libraries.append(row)
        url = page.get('@odata.nextLink')
        if not url:
            return libraries
        # Never send a bearer token to an arbitrary nextLink or Graph endpoint.
        try:
            valid_next = isinstance(url, str) and urlsplit(url)._replace(query='').geturl() == base
        except ValueError:
            valid_next = False
        if not valid_next:
            raise SharePointProbeError('문서함 목록의 다음 페이지 주소가 올바르지 않습니다.')
    raise SharePointProbeError('문서함 목록이 너무 길어 조회를 중단했습니다.')

