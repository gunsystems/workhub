import os
from pathlib import Path
BASE_DIR = Path(__file__).resolve().parent.parent
DEBUG = os.getenv('DEBUG', '1') == '1'
SECRET_KEY = os.getenv('SECRET_KEY', 'local-development-only-workhub')
if not DEBUG and SECRET_KEY == 'local-development-only-workhub':
    raise RuntimeError('SECRET_KEY is required in production')
ALLOWED_HOSTS = os.getenv('ALLOWED_HOSTS', 'localhost,127.0.0.1,testserver' if DEBUG else '').split(',')
if os.getenv('RENDER_EXTERNAL_HOSTNAME'):
    ALLOWED_HOSTS.append(os.environ['RENDER_EXTERNAL_HOSTNAME'])
# Company Workhub subdomain; canonical email/OAuth links switch after DNS and TLS verification.
ALLOWED_HOSTS.append('workhub.gunsystems.co.kr')
INSTALLED_APPS = ['django.contrib.admin','django.contrib.auth','django.contrib.contenttypes','django.contrib.sessions','django.contrib.messages','django.contrib.staticfiles','tasks']
MIDDLEWARE = ['django.middleware.security.SecurityMiddleware','config.canonical.CompanyDomainMiddleware','whitenoise.middleware.WhiteNoiseMiddleware','django.contrib.sessions.middleware.SessionMiddleware','django.middleware.common.CommonMiddleware','django.middleware.csrf.CsrfViewMiddleware','django.contrib.auth.middleware.AuthenticationMiddleware','django.contrib.messages.middleware.MessageMiddleware','django.middleware.clickjacking.XFrameOptionsMiddleware']
ROOT_URLCONF = 'config.urls'
TEMPLATES = [{'BACKEND':'django.template.backends.django.DjangoTemplates','DIRS':[BASE_DIR / 'templates'],'APP_DIRS':True,'OPTIONS':{'context_processors':['django.template.context_processors.request','django.contrib.auth.context_processors.auth','django.contrib.messages.context_processors.messages','tasks.alerts.alert_context','tasks.navigation.navigation','tasks.help_content.page_help']}}]
DATABASES = {'default':{'ENGINE':'django.db.backends.sqlite3','NAME':os.getenv('SQLITE_PATH',str(BASE_DIR/'db.sqlite3'))}}
LANGUAGE_CODE = 'ko-kr'
TIME_ZONE = 'Asia/Seoul'
USE_TZ = True
STATIC_URL = 'static/'
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'
LOGIN_URL = '/login/'
LOGIN_REDIRECT_URL = '/dashboard/'
LOGOUT_REDIRECT_URL = '/login/'

WSGI_APPLICATION = 'config.wsgi.application'
STATIC_ROOT = BASE_DIR / 'staticfiles'
STORAGES = {'default':{'BACKEND':'tasks.drive_storage.WorkhubStorage'},'staticfiles':{'BACKEND':'whitenoise.storage.CompressedManifestStaticFilesStorage'}}
SESSION_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SECURE = not DEBUG
SECURE_SSL_REDIRECT = not DEBUG
SECURE_HSTS_SECONDS = 3600 if not DEBUG else 0
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = 'DENY'
if os.getenv('RENDER') == 'true':
    SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO','https')
AUTH_PASSWORD_VALIDATORS = [
    {'NAME':'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME':'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME':'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME':'django.contrib.auth.password_validation.NumericPasswordValidator'},
]


# Employee invitation and MFA. Existing legacy accounts remain intact.
INSTALLED_APPS += ['accounts', 'django_otp', 'django_otp.plugins.otp_totp']
MIDDLEWARE.insert(MIDDLEWARE.index('django.contrib.auth.middleware.AuthenticationMiddleware') + 1, 'django_otp.middleware.OTPMiddleware')
MIDDLEWARE.insert(MIDDLEWARE.index('django_otp.middleware.OTPMiddleware') + 1, 'accounts.middleware.RequireMFA')
MIDDLEWARE.insert(MIDDLEWARE.index('django.contrib.auth.middleware.AuthenticationMiddleware') + 1, 'accounts.idle.IdleTimeout')
MIDDLEWARE.insert(MIDDLEWARE.index('django.contrib.auth.middleware.AuthenticationMiddleware') + 1, 'tasks.audit.AuditContext')
WORKHUB_EMAIL_DOMAIN = 'gunsystems.co.kr'
WORKHUB_BASE_URL = os.getenv('WORKHUB_BASE_URL', 'https://gunsystems-workhub-test.onrender.com')
HIWORKS_OFFICE_TOKEN = os.getenv('HIWORKS_OFFICE_TOKEN', '')
HIWORKS_USER_ID = os.getenv('HIWORKS_USER_ID', 'admin')
EMAIL_BACKEND = os.getenv('EMAIL_BACKEND', 'accounts.mail_backend.HiworksEmailBackend')
EMAIL_HOST = os.getenv('EMAIL_HOST', 'smtps.hiworks.com')
EMAIL_PORT = int(os.getenv('EMAIL_PORT', '465'))
EMAIL_HOST_USER = os.getenv('EMAIL_HOST_USER', '')
EMAIL_HOST_PASSWORD = os.getenv('EMAIL_HOST_PASSWORD', '')
EMAIL_USE_TLS = os.getenv('EMAIL_USE_TLS', '0') == '1'
EMAIL_USE_SSL = os.getenv('EMAIL_USE_SSL', '1') == '1'
EMAIL_TIMEOUT = 15
DEFAULT_FROM_EMAIL = os.getenv('DEFAULT_FROM_EMAIL', 'GUNSYSTEMS Workhub <workhub@gunsystems.co.kr>')
OTP_TOTP_ISSUER = 'GUNSYSTEMS Workhub'
OTP_TOTP_THROTTLE_FACTOR = 1

PASSWORD_RESET_TIMEOUT = 1800
CSRF_FAILURE_VIEW = 'accounts.views.csrf_failure'
GOOGLE_CALENDAR_CLIENT_ID = os.getenv('GOOGLE_CALENDAR_CLIENT_ID','')
GOOGLE_CALENDAR_CLIENT_SECRET = os.getenv('GOOGLE_CALENDAR_CLIENT_SECRET','')
GOOGLE_TOKEN_ENCRYPTION_KEY = os.getenv('GOOGLE_TOKEN_ENCRYPTION_KEY','')
WORKHUB_SCHEDULER_ENABLED = os.getenv('WORKHUB_SCHEDULER_ENABLED','1') == '1'
KASI_SERVICE_KEY = os.getenv('KASI_SERVICE_KEY','')
WORKHUB_HOLIDAY_SYNC_ENABLED = os.getenv('WORKHUB_HOLIDAY_SYNC_ENABLED','1') == '1'

# Independent minimum-scope tokens: never broaden the existing authentication mail token.
HIWORKS_DIRECTORY_TOKEN = os.getenv('HIWORKS_DIRECTORY_TOKEN', '')
HIWORKS_NOTIFY_TOKEN = os.getenv('HIWORKS_NOTIFY_TOKEN', '')

# Company staff access requires a recent authoritative Hiworks directory match.
# Local development/test fixtures opt in explicitly; production defaults to enforced.
WORKHUB_REQUIRE_HIWORKS = os.getenv('WORKHUB_REQUIRE_HIWORKS', '0' if DEBUG else '1') == '1'
MEDIA_ROOT = Path(os.getenv('WORKHUB_UPLOAD_PATH', str(Path(DATABASES['default']['NAME']).resolve().parent / 'private-uploads')))
FILE_UPLOAD_MAX_MEMORY_SIZE = 1024 * 1024
DATA_UPLOAD_MAX_MEMORY_SIZE = 12 * 1024 * 1024
GOOGLE_DRIVE_CREDENTIALS_FILE = os.getenv('GOOGLE_DRIVE_CREDENTIALS_FILE', '')
GOOGLE_DRIVE_SHARED_DRIVE_ID = os.getenv('GOOGLE_DRIVE_SHARED_DRIVE_ID', '')
WORKHUB_DOCUMENT_MAX_BYTES = 20 * 1024 * 1024
LOGGING = {'version':1,'disable_existing_loggers':False,
    'filters':{'subscription_tokens':{'()':'config.log_filters.SubscriptionTokenFilter'}},
    'loggers':{'django.request':{'filters':['subscription_tokens']},'django.server':{'filters':['subscription_tokens']}}}
