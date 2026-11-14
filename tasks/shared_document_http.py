"""Bounded Graph writes; never retry a mutation or disclose provider payloads."""
import json
from urllib.error import HTTPError, URLError
from urllib.request import Request, build_opener
from .google_calendar import NoRedirect
from .sharepoint_probe import SharePointProbeError


def graph_write(url, token, method, data=None, etag=None):
    headers = {'Authorization': 'Bearer ' + token, 'Accept': 'application/json'}
    body = None
    if data is not None:
        body = json.dumps(data).encode()
        headers['Content-Type'] = 'application/json'
    if etag:
        headers['If-Match'] = etag
    try:
        with build_opener(NoRedirect()).open(Request(url, data=body, headers=headers, method=method), timeout=12) as response:
            if response.status == 204:
                return {}
            raw = response.read(1_000_001)
        if len(raw) > 1_000_000:
            raise ValueError
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise ValueError
        return value
    except HTTPError as exc:
        reason = {403: '폴더를 변경할 SharePoint 권한이 없습니다. 관리자에게 문의하세요.',
                  401: '문서 계정을 다시 연결해 주세요.', 404: '폴더가 이동되거나 삭제되었습니다.',
                  409: '같은 이름의 폴더가 이미 있거나 폴더 상태가 변경되었습니다.',
                  412: '다른 사용자가 폴더를 변경했습니다. 새로고침 후 다시 진행하세요.',
                  429: 'Microsoft 요청이 많습니다. 잠시 후 다시 진행하세요.'}
        raise SharePointProbeError(reason.get(exc.code, '폴더 변경에 실패했습니다. 새로고침하여 결과를 확인하세요.')) from None
    except (URLError, OSError, ValueError, TypeError):
        raise SharePointProbeError('변경 결과를 확인하지 못했습니다. 중복 실행하지 말고 새로고침하여 확인하세요.') from None
