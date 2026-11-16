"""Thread-local connection pooling for Graph reads, never an identity/data cache."""
from http.cookiejar import CookieJar, DefaultCookiePolicy
from threading import local
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit

import requests


_local = local()


class NoCookies(DefaultCookiePolicy):
    def set_ok(self, cookie, request):
        return False

    def return_ok(self, cookie, request):
        return False


def session():
    if not hasattr(_local, 'session'):
        client = requests.Session()
        client.cookies = CookieJar(policy=NoCookies())
        client.mount('https://', requests.adapters.HTTPAdapter(
            pool_connections=2, pool_maxsize=2, max_retries=0))
        _local.session = client
    return _local.session


def read_graph(url, *, token=None, json_data=None):
    parsed = urlsplit(url)
    if (parsed.scheme != 'https' or parsed.netloc != 'graph.microsoft.com' or
            not parsed.path.startswith('/v1.0/') or parsed.fragment):
        raise ValueError('Invalid Graph endpoint')
    if json_data is not None:
        if (parsed.path != '/v1.0/$batch' or parsed.query or
                not isinstance(json_data, dict) or not isinstance(json_data.get('requests'), list) or
                not 1 <= len(json_data['requests']) <= 20 or
                any(not isinstance(row, dict) or set(row) - {'id', 'method', 'url'} or row.get('method') != 'GET' or
                    not isinstance(row.get('url'), str) or not row['url'].startswith('/drives/')
                    for row in json_data['requests'])):
            raise ValueError('Only read batches are allowed')
    headers = {'Accept': 'application/json'}
    if token:
        headers['Authorization'] = 'Bearer ' + token
    # Prepare independently: session auth, cookies and headers never flow into
    # another user's request. Only the underlying TLS/TCP pool is reused.
    prepared = requests.Request('POST' if json_data is not None else 'GET', url,
        headers=headers, json=json_data).prepare()
    client = session()
    options = client.merge_environment_settings(url, {}, True, None, None)
    try:
        with client.send(prepared, timeout=(12, 12), allow_redirects=False, **options) as response:
            if response.status_code != 200:
                raise HTTPError(url, response.status_code, 'Graph read rejected', {}, None)
            chunks, size = [], 0
            for chunk in response.iter_content(chunk_size=65536):
                size += len(chunk)
                if size > 1_000_000:
                    raise ValueError('Response too large')
                chunks.append(chunk)
            return b''.join(chunks)
    except requests.RequestException:
        raise URLError('Microsoft connection failed') from None
