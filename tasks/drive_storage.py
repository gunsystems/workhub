"""Private Drive storage. Legacy local names remain readable; no public URLs."""
import io
import hashlib
import json
import re
from pathlib import Path
from urllib.parse import urlparse

from django.conf import settings
from django.core.files.base import File
from django.core.files.storage import FileSystemStorage, Storage
from django.utils.deconstruct import deconstructible


class RepositoryError(Exception):
    """Deliberately excludes upstream response bodies, credentials and session URLs."""


def configured():
    return bool(settings.GOOGLE_DRIVE_CREDENTIALS_FILE and settings.GOOGLE_DRIVE_SHARED_DRIVE_ID)


def response_json(response):
    try:
        data=response.json()
        if not isinstance(data,dict):raise ValueError
        return data
    except (ValueError,TypeError):
        raise RepositoryError('자료 저장소 응답을 확인하지 못했습니다. 잠시 후 다시 시도해 주세요.') from None


class DriveClient:
    def __init__(self):
        try:
            from google.oauth2 import service_account
            from google.auth.transport.requests import AuthorizedSession
            with open(settings.GOOGLE_DRIVE_CREDENTIALS_FILE, encoding='utf-8') as source:
                info = json.load(source)
            # Credential files are administrator supplied, but endpoints still stay fixed.
            if info.get('type') != 'service_account' or info.get('token_uri') != 'https://oauth2.googleapis.com/token':
                raise ValueError('Unexpected credential type or endpoint')
            credentials = service_account.Credentials.from_service_account_info(
                info, scopes=['https://www.googleapis.com/auth/drive'])
            self.session = AuthorizedSession(credentials, refresh_timeout=15)
        except Exception:
            raise RepositoryError('자료 저장소 인증 설정을 확인해 주세요.') from None
        self.drive_id = settings.GOOGLE_DRIVE_SHARED_DRIVE_ID

    def request(self, method, path, **kwargs):
        try:
            response = self.session.request(method, 'https://www.googleapis.com/' + path,
                timeout=(10, 60), allow_redirects=False, **kwargs)
            if not 200 <= response.status_code < 300:
                response.close()
                raise RepositoryError('자료 저장소에 연결하지 못했습니다. 잠시 후 다시 시도하거나 관리자에게 문의하세요.')
            return response
        except RepositoryError:
            raise
        except Exception:
            raise RepositoryError('자료 저장소 응답을 받지 못했습니다. 잠시 후 다시 시도해 주세요.') from None

    def check(self):
        with self.request('GET', 'drive/v3/files/' + self.drive_id,
                params={'supportsAllDrives':'true','fields':'id,name,mimeType,capabilities(canAddChildren)'}) as response:
            data = response_json(response)
        if data.get('mimeType') != 'application/vnd.google-apps.folder' or not data.get('capabilities',{}).get('canAddChildren'):
            raise RepositoryError('공유 드라이브의 파일 추가 권한을 확인해 주세요.')
        return data.get('name', '업무자료')

    def upload(self, name, content):
        # Bounded uploads avoid keeping large files in the web worker's memory.
        content.seek(0)
        size = content.size
        if size > settings.WORKHUB_DOCUMENT_MAX_BYTES:
            raise RepositoryError('파일당 20MB 이하로 등록해 주세요.')
        digest=hashlib.md5()
        for chunk in content.chunks():digest.update(chunk)
        content.seek(0)
        with self.request('POST', 'upload/drive/v3/files',
                params={'uploadType':'resumable','supportsAllDrives':'true','fields':'id,size,md5Checksum'},
                json={'name':Path(name).name, 'parents':[self.drive_id],
                    'appProperties':{'workhub':'attachment'}},
                headers={'X-Upload-Content-Type':'application/octet-stream','X-Upload-Content-Length':str(size)}) as response:
            location = response.headers.get('Location','')
        endpoint = urlparse(location)
        if endpoint.scheme != 'https' or endpoint.netloc != 'www.googleapis.com' or not endpoint.path.startswith('/upload/drive/v3/files'):
            raise RepositoryError('자료 저장소의 업로드 응답이 올바르지 않습니다.')
        # Convert only the fixed trusted host to request()'s relative path.
        relative = endpoint.path.lstrip('/') + ('?' + endpoint.query if endpoint.query else '')
        with self.request('PUT', relative, data=content,
                headers={'Content-Type':'application/octet-stream','Content-Length':str(size)}) as response:
            result = response_json(response)
        file_id = result.get('id','')
        if (not re.fullmatch(r'[A-Za-z0-9_-]+', file_id) or str(result.get('size')) != str(size)
                or result.get('md5Checksum') != digest.hexdigest()):
            raise RepositoryError('파일 저장 결과를 확인하지 못했습니다. 관리자에게 문의하세요.')
        return 'drive/' + file_id

    def open(self, file_id):
        if not re.fullmatch(r'[A-Za-z0-9_-]+', file_id):
            raise RepositoryError('잘못된 파일 식별자입니다.')
        with self.request('GET', 'drive/v3/files/' + file_id,
                params={'supportsAllDrives':'true','fields':'id,driveId,trashed,size,appProperties'}) as response:
            meta = response_json(response)
        if meta.get('driveId') != self.drive_id or meta.get('trashed') or meta.get('appProperties',{}).get('workhub') != 'attachment':
            raise RepositoryError('자료 저장소에서 파일을 찾을 수 없습니다.')
        try:size=int(meta.get('size',0))
        except (ValueError,TypeError):raise RepositoryError('파일 크기를 확인하지 못했습니다.') from None
        if size > settings.WORKHUB_DOCUMENT_MAX_BYTES:
            raise RepositoryError('파일 크기 제한을 초과했습니다.')
        response = self.request('GET', 'drive/v3/files/' + file_id,
            params={'alt':'media','supportsAllDrives':'true'}, stream=True)
        return response


@deconstructible
class WorkhubStorage(Storage):
    def get_available_name(self, name, max_length=None):
        return name

    def _save(self, name, content):
        if configured():
            return DriveClient().upload(name, content)
        # A partially configured production storage must never silently use local disk.
        if (settings.GOOGLE_DRIVE_CREDENTIALS_FILE or settings.GOOGLE_DRIVE_SHARED_DRIVE_ID
                or (not settings.DEBUG and settings.WORKHUB_REQUIRE_HIWORKS)):
            raise RepositoryError('자료 저장소 설정이 완료되지 않았습니다.')
        return FileSystemStorage().save(name, content)

    def _open(self, name, mode='rb'):
        if not name.startswith('drive/'):
            return FileSystemStorage().open(name, mode)
        response = DriveClient().open(name[6:])
        try:
            data = bytearray()
            for chunk in response.iter_content(64 * 1024):
                data.extend(chunk)
                if len(data) > settings.WORKHUB_DOCUMENT_MAX_BYTES:
                    raise RepositoryError('파일 크기 제한을 초과했습니다.')
            return File(io.BytesIO(data), name=name)
        except RepositoryError:
            raise
        except Exception:
            raise RepositoryError('파일 전송 중 연결이 끊겼습니다. 다시 내려받아 주세요.') from None
        finally:
            response.close()

    def url(self, name):
        raise NotImplementedError('Private files must be downloaded through Workhub permission checks.')

    def delete(self, name):
        raise NotImplementedError('Attachments are archived with their audit trail, never purged by storage hooks.')
