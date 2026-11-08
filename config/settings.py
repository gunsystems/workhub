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
MIDDLEWARE = ['django.middleware.security.SecurityMiddleware','whitenoise.middleware.WhiteNoiseMiddleware','django.contrib.sessions.middleware.SessionMiddleware','django.middleware.common.CommonMiddleware','django.middleware.csrf.CsrfViewMiddleware','django.contrib.auth.middleware.AuthenticationMiddleware','django.contrib.messages.middleware.MessageMiddleware','django.middleware.clickjacking.XFrameOptionsMiddleware']
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
STORAGES = {'default':{'BACKEND':'django.core.files.storage.FileSystemStorage'},'staticfiles':{'BACKEND':'whitenoise.storage.CompressedManifestStaticFilesStorage'}}
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
