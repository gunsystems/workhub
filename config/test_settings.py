"""Local/CI tests only; never use this settings module on a deployed service."""
from .settings import *

PASSWORD_HASHERS = ['django.contrib.auth.hashers.MD5PasswordHasher']
EMAIL_BACKEND = 'django.core.mail.backends.locmem.EmailBackend'
