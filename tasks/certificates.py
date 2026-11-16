"""Issue files separately from accepting requests; no company-wide HR visibility."""
from pathlib import Path
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from accounts.lifecycle import system_admin, account_allowed
from .models import CertificatePolicy, CertificateRequest, CertificateEvent, CertificateNotice
from .permissions import profile

OPEN = ('pending', 'processing')
FALLBACK = Q(assignee__isnull=True) | Q(assignee__is_active=False) | Q(assignee__employee__is_active=False)


def handler_allowed(user):
    return bool(user and user.is_authenticated and account_allowed(user) and (profile(user) or system_admin(user)))


def can_handle(user, item):
    return bool(handler_allowed(user) and (system_admin(user) or item.assignee_id == user.pk))


def visible(user):
    qs = CertificateRequest.objects.select_related('employee__department', 'assignee', 'issued_by')
    if not handler_allowed(user):
        return qs.none()
    if system_admin(user):
        return qs
    person = profile(user)
    return qs.filter(Q(employee=person) | Q(assignee=user)).distinct()


def pending_for(user):
    qs = visible(user).filter(state__in=OPEN)
    return qs.filter(Q(assignee=user) | FALLBACK) if system_admin(user) else qs.filter(assignee=user)


def eligible_users():
    return get_user_model().objects.filter(is_active=True).filter(
        Q(is_superuser=True) | Q(employee__is_active=True)).order_by('username').distinct()


def recipients(item):
    if item.assignee_id and handler_allowed(item.assignee):
        return [item.assignee]
    return [u for u in get_user_model().objects.filter(is_active=True, is_staff=True) if system_admin(u)]


def event(item, user, label, body='', notify=''):
    row = CertificateEvent.objects.create(request=item, actor=user, action=label,
        body=body[:1000], state=item.state, revision=item.revision)
    from .audit import write
    # Global audit contains workflow metadata, not the personal document or purpose.
    write('change', 'CertificateRequest', item.pk, '증명서·서류 신청',
        after={'처리': label, '상태': item.state, '버전': item.revision}, actor=user)
    users = recipients(item) if notify == 'pending' else []
    if notify == 'result' and item.employee.user_id:
        users = [item.employee.user]
    for recipient in users:
        if recipient.is_active:
            CertificateNotice.objects.get_or_create(event=row, user=recipient, kind=notify,
                defaults={'email': recipient.email.lower(), 'message':
                    '증명서·서류 신청을 처리해 주세요.' if notify == 'pending' else
                    ('신청한 서류가 발급되었습니다.' if item.state == 'issued' else '서류 신청 처리 결과를 확인해 주세요.')})
    return row


def snapshot(person):
    employment = getattr(person, 'employment', None)
    return {'name': person.name, 'department': str(person.department),
            'staff_number': employment.staff_number if employment else '',
            'job_title': employment.job_title if employment else '',
            'joined_on': str(employment.joined_on) if employment and employment.joined_on else ''}


@transaction.atomic
def submit(user, key, data):
    person = profile(user)
    if not person or user.is_superuser or not account_allowed(user):
        raise PermissionDenied
    policy, _ = CertificatePolicy.objects.get_or_create(pk=1)
    policy = CertificatePolicy.objects.select_for_update().get(pk=policy.pk)
    previous = CertificateRequest.objects.filter(submission_key=key).first()
    if previous:
        if previous.employee_id != person.pk:
            raise PermissionDenied
        return previous
    assignee = policy.assignee if policy.assignee_id and handler_allowed(policy.assignee) else None
    item = CertificateRequest(employee=person, assignee=assignee, submission_key=key,
        employee_snapshot=snapshot(person), **data)
    if not recipients(item):
        raise ValidationError('접수 가능한 관리자가 없습니다. 관리자에게 문의해 주세요.')
    item.full_clean()
    item.save()
    event(item, user, '서류 신청', notify='pending')
    return item


def validate_pdf(upload):
    if (not upload or Path(upload.name).suffix.casefold() != '.pdf' or
            not 0 < upload.size <= 20 * 1024 * 1024):
        raise ValidationError('발급할 PDF 파일을 첨부하세요. 파일당 최대 20MB입니다.')
    header = upload.read(8)
    upload.seek(0)
    if not header.startswith(b'%PDF-'):
        raise ValidationError('정상적인 PDF 파일을 첨부하세요.')


def act(pk, user, action, revision, body='', upload=None, assignee=None):
    saved_name = None
    storage = None
    try:
        with transaction.atomic():
            item = CertificateRequest.objects.select_for_update().select_related('employee__user', 'assignee').get(pk=pk)
            if not visible(user).filter(pk=pk).exists():
                raise PermissionDenied
            if item.revision != revision:
                raise ValidationError('이미 변경된 신청입니다. 새로고침 후 다시 확인하세요.')
            owner = item.employee.user_id == user.pk
            notify = ''
            if action == 'cancel':
                if not owner or item.state != 'pending':
                    raise PermissionDenied
                item.state, label = 'cancelled', '신청 취소'
            else:
                if not can_handle(user, item):
                    raise PermissionDenied
                if action == 'reassign':
                    if not system_admin(user) or item.state not in OPEN:
                        raise PermissionDenied
                    if assignee and not handler_allowed(assignee):
                        raise ValidationError('사용 가능한 담당자를 선택하세요.')
                    item.assignee = assignee
                    label, notify = '담당자 재배정', 'pending'
                elif action == 'start':
                    if item.state != 'pending':
                        raise ValidationError('접수 대기 중인 신청만 확인할 수 있습니다.')
                    item.state, label = 'processing', '접수 확인'
                elif action == 'reject':
                    if item.state not in OPEN or not body.strip():
                        raise ValidationError('처리 가능한 신청인지 확인하고 반려 사유를 입력하세요.')
                    item.state, label, notify = 'rejected', '반려', 'result'
                elif action == 'issue':
                    if item.state not in OPEN:
                        raise ValidationError('이미 처리된 신청입니다. 목록을 새로고침하세요.')
                    validate_pdf(upload)
                    item.issued_name = f'{item.number}_{item.title}.pdf'
                    item.issued_file.save('issued.pdf', upload, save=False)
                    saved_name, storage = item.issued_file.name, item.issued_file.storage
                    item.state, label, notify = 'issued', '서류 발급', 'result'
                    item.issued_at, item.issued_by = timezone.now(), user
                elif action == 'revoke':
                    if item.state != 'issued' or not body.strip():
                        raise ValidationError('발급 완료된 신청인지 확인하고 취소 사유를 입력하세요.')
                    item.state, label, notify = 'revoked', '발급 취소', 'result'
                else:
                    raise ValidationError('처리 항목을 확인하세요.')
            item.revision += 1
            item.updated_at = timezone.now()
            item.save()
            event(item, user, label, body.strip(), notify)
            return item
    except Exception:
        if saved_name and storage:
            storage.delete(saved_name)
        raise


@transaction.atomic
def set_policy(user, assignee, revision, transfer_open=False):
    if not system_admin(user):
        raise PermissionDenied
    policy, _ = CertificatePolicy.objects.get_or_create(pk=1)
    policy = CertificatePolicy.objects.select_for_update().get(pk=policy.pk)
    if policy.revision != revision:
        raise ValidationError('담당자 설정이 변경되었습니다. 새로고침하세요.')
    if assignee and not handler_allowed(assignee):
        raise ValidationError('사용 가능한 담당자를 선택하세요.')
    policy.assignee, policy.revision = assignee, revision + 1
    policy.save()
    count = 0
    if transfer_open:
        for item in CertificateRequest.objects.select_for_update().filter(state__in=OPEN):
            if item.assignee_id == policy.assignee_id:
                continue
            item.assignee = assignee
            item.revision += 1
            item.updated_at = timezone.now()
            item.save(update_fields=['assignee','revision','updated_at'])
            event(item, user, '담당자 재배정', '기본 담당자 변경 시 진행 중 신청 함께 변경', 'pending')
            count += 1
    from .audit import write
    write('change', 'CertificatePolicy', 1, '서류 발급담당자 설정', after={
        '담당자': assignee.pk if assignee else '관리자 접수', '재배정 건수': count}, actor=user)
    return count


def deliver_pending(now=None):
    from accounts.services import deliver
    from django.conf import settings
    from django.urls import reverse
    from django.utils.html import format_html
    for row in CertificateNotice.objects.filter(mail_state='pending').select_related('user', 'event__request__employee')[:50]:
        if not CertificateNotice.objects.filter(pk=row.pk, mail_state='pending').update(mail_state='sending'):
            continue
        user, item = row.user, row.event.request
        access = getattr(user, 'workhub_access', None)
        allowed = bool(handler_allowed(user) and user.email and access and access.verified_at and
            user.email.lower() == access.email.lower() == row.email and visible(user).filter(pk=item.pk).exists())
        if row.kind == 'pending':
            allowed = allowed and pending_for(user).filter(pk=item.pk, revision=row.event.revision).exists()
        if row.kind == 'result':
            allowed = allowed and item.revision == row.event.revision
        if not allowed:
            CertificateNotice.objects.filter(pk=row.pk).update(mail_state='skipped')
            continue
        url = settings.WORKHUB_BASE_URL.rstrip('/') + reverse('certificate_detail', args=[item.pk])
        try:
            deliver('[Workhub] ' + row.message, row.message + '\n로그인 후 신청내역에서 확인하세요.\n' + url, user.email, html_body=format_html('<p>{}</p><p>로그인 후 신청내역에서 확인하세요.</p><p><a href="{}">서류 신청 확인하기</a></p>', row.message, url))
        except Exception:
            state = 'unknown'
        else:
            state = 'sent'
        CertificateNotice.objects.filter(pk=row.pk).update(mail_state=state)
