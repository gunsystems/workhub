"""Fail-closed company membership checks, independent of notification settings."""
import secrets
from datetime import timedelta
from django.conf import settings
from django.core.exceptions import ValidationError
from django.utils import timezone
from tasks.models import HiworksDirectoryCache, SchedulerLease

TTL=timedelta(minutes=5)


def fresh_cache():
    cache=HiworksDirectoryCache.objects.filter(key='access').first()
    return cache if cache and cache.checked_at and timezone.now()-TTL <= cache.checked_at <= timezone.now() else None


def refresh_directory(force=False):
    if not settings.WORKHUB_REQUIRE_HIWORKS: return True
    now=timezone.now()
    cache=HiworksDirectoryCache.objects.filter(key='access').first()
    if not force and cache and cache.checked_at and now-timedelta(minutes=2) <= cache.checked_at <= now: return True
    # Bound retries and concurrent refreshes globally, including the authenticated retry button.
    token=secrets.token_hex(16)
    SchedulerLease.objects.get_or_create(key='hiworks-access',defaults={'expires_at':now})
    if not SchedulerLease.objects.filter(key='hiworks-access',expires_at__lte=now).update(token=token,expires_at=now+timedelta(minutes=1)):
        return bool(fresh_cache())
    try:
        from tasks.hiworks import directory_rows
        rows=directory_rows()
        HiworksDirectoryCache.objects.update_or_create(key='access',defaults={'rows':rows,'checked_at':now,'attempted_at':now,'error':''})
        from tasks.models import HiworksLink
        from .lifecycle import revoke_sessions
        current_ids={row['remote_id'] for row in rows}
        revoked_users=HiworksLink.objects.exclude(remote_id__in=current_ids).filter(employee__user__is_superuser=False).exclude(employee__user=None).values_list('employee__user_id',flat=True)
        for user_id in revoked_users: revoke_sessions(user_id)
        return True
    except ValidationError as exc:
        cache,_=HiworksDirectoryCache.objects.get_or_create(key='access')
        cache.attempted_at=now;cache.error=exc.messages[0][:240];cache.save(update_fields=['attempted_at','error'])
        return False
    finally:
        SchedulerLease.objects.filter(key='hiworks-access',token=token).update(token='',expires_at=timezone.now()+timedelta(seconds=30))


def directory_entry(email):
    value=email.strip().lower()
    if not value.endswith('@'+settings.WORKHUB_EMAIL_DOMAIN): return None
    cache=fresh_cache()
    if not cache: return None
    matches=[row for row in cache.rows if row.get('email','').lower()==value and row.get('remote_id')]
    return matches[0] if len(matches)==1 else None


def company_mailbox(email):
    return bool(directory_entry(email))


def allowed(user):
    if not settings.WORKHUB_REQUIRE_HIWORKS: return True
    if not user or not user.is_authenticated or not user.is_active: return False
    # Only the original top-level operator is the recovery exception; sub-admins are employees.
    if user.is_superuser: return True
    employee=getattr(user,'employee',None)
    access=getattr(user,'workhub_access',None)
    if not (employee and employee.is_active and access and access.verified_at and user.email.strip().lower()==access.email.strip().lower()): return False
    row=directory_entry(access.email)
    from tasks.models import HiworksLink
    link=HiworksLink.objects.filter(employee=employee).first()
    return bool(row and link and link.email.lower()==access.email.lower() and link.remote_id==row['remote_id'] and link.user_id.lower()==row['user_id'].lower())


def require_mailbox(email):
    if not settings.WORKHUB_REQUIRE_HIWORKS: return
    if not fresh_cache(): refresh_directory()
    if not fresh_cache(): raise ValidationError('하이웍스 직원 등록 여부를 확인하지 못했습니다. 관리자가 연동 상태를 확인한 뒤 다시 시도해 주세요.')
    if not company_mailbox(email): raise ValidationError('하이웍스에 등록된 회사 이메일만 초대·가입할 수 있습니다. 회사 계정 등록 여부를 확인해 주세요.')
