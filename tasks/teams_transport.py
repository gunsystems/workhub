"""Bot Connector REST protocol, including signed channel/service URL validation.

See docs/TEAMS_NOTIFICATIONS.md for the Microsoft protocol references.
No Graph permissions, chat history, or legacy connector webhook is used.
"""
import hashlib
import json
import re
from urllib.parse import urlsplit, quote
from uuid import UUID
import jwt
import requests
from django.conf import settings
from django.core.cache import cache

KEYS_URL = 'https://login.botframework.com/v1/.well-known/keys'
ISSUER = 'https://api.botframework.com'
MAX_KEYS_BYTES = 2 * 1024 * 1024
MAX_SIGNING_KEYS = 1024


def config():
    try:
        tenant = str(UUID(settings.TEAMS_NOTIFICATION_TENANT_ID))
        app = str(UUID(settings.TEAMS_NOTIFICATION_APP_ID))
    except (ValueError, AttributeError, TypeError):
        return None
    secret = settings.TEAMS_NOTIFICATION_CLIENT_SECRET
    return (tenant, app, secret) if secret else None


def enabled():
    return bool(settings.WORKHUB_TEAMS_NOTIFICATIONS_ENABLED and config())


def safe_service_url(value):
    if not isinstance(value, str) or len(value) > 500:
        raise ValueError('service')
    parsed = urlsplit(value)
    if (parsed.scheme != 'https' or parsed.netloc != 'smba.trafficmanager.net' or
            parsed.query or parsed.fragment or not re.fullmatch(r'/[A-Za-z0-9/_-]*', parsed.path) or
            '//' in parsed.path):
        raise ValueError('service')
    return value.rstrip('/') + '/'


def _keys(refresh=False):
    key = 'workhub:teams:signing-keys:v1'
    keys = None if refresh else cache.get(key)
    if keys is None:
        # Microsoft's rolling key set can contain hundreds of endorsed keys.
        # Bound the streamed body before parsing, without truncating valid rotations.
        with requests.get(KEYS_URL, timeout=5, allow_redirects=False, stream=True) as response:
            response.raise_for_status()
            if response.status_code != 200:
                raise ValueError('keys')
            body = bytearray()
            for chunk in response.iter_content(chunk_size=16384):
                body.extend(chunk)
                if len(body) > MAX_KEYS_BYTES:
                    raise ValueError('keys')
            keys = json.loads(body)['keys']
        if not isinstance(keys, list) or not keys or len(keys) > MAX_SIGNING_KEYS or any(
                not isinstance(key, dict) for key in keys):
            raise ValueError('keys')
        cache.set(key, keys, 3600)
    return keys


def validate_activity(authorization, activity):
    configured = config()
    if not configured or not enabled():
        raise ValueError('disabled')
    tenant, app, _ = configured
    if not isinstance(authorization, str) or not authorization.startswith('Bearer '):
        raise ValueError('bearer')
    token = authorization[7:]
    if len(token) > 16000:
        raise ValueError('token')
    header = jwt.get_unverified_header(token)
    if header.get('alg') != 'RS256' or not isinstance(header.get('kid'), str) or len(header['kid']) > 200:
        raise ValueError('algorithm')
    signing = next((k for k in _keys() if k.get('kid') == header['kid']), None)
    if signing is None:
        # One refresh per minute prevents attacker-controlled key IDs flooding Microsoft.
        if cache.add('workhub:teams:keys-refresh', True, 60):
            signing = next((k for k in _keys(True) if k.get('kid') == header['kid']), None)
    if signing is None or 'msteams' not in signing.get('endorsements', []):
        raise ValueError('endorsement')
    claims = jwt.decode(token, jwt.PyJWK.from_dict(signing).key, algorithms=['RS256'],
        audience=app, issuer=ISSUER, leeway=300,
        options={'require': ['iss', 'aud', 'exp', 'nbf']})
    service = safe_service_url(activity.get('serviceUrl'))
    signed_service = claims.get('serviceurl', claims.get('serviceUrl'))
    if not isinstance(signed_service, str) or service != safe_service_url(signed_service):
        raise ValueError('service claim')
    conversation = activity.get('conversation') or {}
    channel = activity.get('channelData') or {}
    if (activity.get('channelId') != 'msteams' or conversation.get('conversationType') != 'personal' or
            str(UUID(channel.get('tenant', {}).get('id', ''))) != tenant or
            str(UUID(conversation.get('tenantId', tenant))) != tenant or
            activity.get('recipient', {}).get('id') != '28:' + app):
        raise ValueError('scope')
    conversation_id = conversation.get('id')
    if not isinstance(conversation_id, str) or not 1 <= len(conversation_id) <= 500:
        raise ValueError('conversation')
    return tenant, app, service, conversation_id


def access_token():
    tenant, app, secret = config()
    # The credential fingerprint prevents using a cached token after secret rotation.
    key = 'workhub:teams:access:' + hashlib.sha256((tenant + app + secret).encode()).hexdigest()
    token = cache.get(key)
    if token:
        return token
    response = requests.post(f'https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token',
        data={'grant_type': 'client_credentials', 'client_id': app, 'client_secret': secret,
              'scope': 'https://api.botframework.com/.default'}, timeout=8, allow_redirects=False)
    response.raise_for_status()
    if response.status_code != 200:
        raise ValueError('token response')
    data = response.json()
    token = data['access_token']
    if not isinstance(token, str) or not token:
        raise ValueError('token response')
    cache.set(key, token, max(1, min(int(data.get('expires_in', 300)) - 60, 3300)))
    return token


def send(connection, title, url):
    endpoint = safe_service_url(connection.service_url) + 'v3/conversations/' + quote(connection.conversation, safe='') + '/activities'
    card = {'type': 'AdaptiveCard', 'version': '1.2', '$schema': 'http://adaptivecards.io/schemas/adaptive-card.json',
        'body': [{'type': 'TextBlock', 'text': 'WORKHUB', 'weight': 'Bolder', 'color': 'Accent'},
                 {'type': 'TextBlock', 'text': title, 'wrap': True, 'size': 'Medium'},
                 {'type': 'TextBlock', 'text': '내용 확인과 처리는 Workhub에서 진행하세요.', 'wrap': True}],
        'actions': [{'type': 'Action.OpenUrl', 'title': 'Workhub에서 확인', 'url': url}]}
    return requests.post(endpoint, headers={'Authorization': 'Bearer ' + access_token()},
        json={'type': 'message', 'text': title, 'summary': title,
              'attachments': [{'contentType': 'application/vnd.microsoft.card.adaptive', 'content': card}]},
        timeout=8, allow_redirects=False)
