"""Employee access lifecycle; business records never cascade through account deletion."""
from django.contrib.auth import get_user_model
from django.contrib.sessions.models import Session
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from tasks.models import Employee, Task, Project, TaskEvent, EmployeeFeedback, WorkRequest
from .models import AccountEvent, Invitation, RateBucket
from .services import digest


ACTIONS = dict(AccountEvent.ACTION_CHOICES)


def account_allowed(user):
    if not user or not user.is_active:
        return False
    employee = getattr(user, 'employee', None)
    return employee is None or employee.is_active


def business_counts(employee):
    user_id = employee.user_id
    return {
        '담당 업무': Task.objects.filter(assignee=employee).count(),
        '총괄 프로젝트': Project.objects.filter(owner=employee).count(),
        '받은 종합 피드백': EmployeeFeedback.objects.filter(employee=employee).count(),
        '작성한 업무 보고·피드백': TaskEvent.objects.filter(author_id=user_id).count() if user_id else 0,
        '작성한 종합 피드백': EmployeeFeedback.objects.filter(author_id=user_id).count() if user_id else 0,
        '접수한 업무 요청': WorkRequest.objects.filter(creator_id=user_id).count() if user_id else 0,
    }


def revoke_sessions(user_id):
    if not user_id:
        return 0
    keys = []
    for session in Session.objects.filter(expire_date__gt=timezone.now()).iterator():
        data = session.get_decoded()
        if str(data.get('_auth_user_id')) == str(user_id) or str(data.get('pending_mfa_user')) == str(user_id):
            keys.append(session.session_key)
    return Session.objects.filter(session_key__in=keys).delete()[0]


def forget_limits(employee, email, invitation_id, user_id):
    keys = [f'invite:{employee.pk}']
    if email:
        keys += [f'login-id:{email.lower()}', f'reset-email:{email.lower()}']
    if invitation_id:
        keys += [f'code:{invitation_id}']
    if user_id:
        keys += [f'otp:{user_id}', f'otp-setup:{user_id}', f'security:{user_id}']
    RateBucket.objects.filter(key__in=[digest(key) for key in keys]).delete()


@transaction.atomic
def change_access(actor, employee_id, action, reason, confirmation=''):
    if not actor.is_active or not actor.is_superuser:
        raise PermissionDenied
    employee = Employee.objects.select_for_update().select_related('user').get(pk=employee_id)
    user = get_user_model().objects.select_for_update().get(pk=employee.user_id) if employee.user_id else None
    if user and (user.pk == actor.pk or user.is_superuser):
        raise ValidationError('본인 또는 최상위 관리자 계정은 직원 계정 관리에서 변경할 수 없습니다.')
    if action not in ACTIONS or not reason.strip():
        raise ValidationError('처리 항목과 사유를 입력하세요.')
    invitation = Invitation.objects.select_for_update().filter(employee=employee).first()
    email = (getattr(getattr(user, 'workhub_access', None), 'email', '') or
             (user.email if user else '') or (invitation.email if invitation else ''))
    user_id = user.pk if user else None
    if action in ['delete_access', 'delete_employee']:
        expected = email or employee.name
        if confirmation.strip().casefold() != expected.casefold():
            raise ValidationError('삭제 확인란에 표시된 회사 이메일 또는 직원 이름을 정확히 입력하세요.')
        if action == 'delete_employee' and any(business_counts(employee).values()):
            raise ValidationError('업무 이력이 있어 직원을 완전히 삭제할 수 없습니다. 로그인 계정 삭제 또는 사용 중지를 선택하세요.')
        if action == 'delete_access' and not user and not invitation:
            raise ValidationError('삭제할 로그인 계정이나 초대 정보가 없습니다.')
    if action == 'reactivate':
        if employee.is_active and (not user or user.is_active):
            raise ValidationError('이미 사용 가능한 직원입니다.')
        employee.is_active = True
        employee.save(update_fields=['is_active'])
        if user:
            user.is_active = True
            user.save(update_fields=['is_active'])
    else:
        # Revocation happens before deletion and includes pending second-factor sessions.
        revoke_sessions(user_id)
        employee.is_active = False
        employee.save(update_fields=['is_active'])
        if action == 'suspend':
            if user:
                user.is_active = False
                user.save(update_fields=['is_active'])
            if invitation and not invitation.used_at:
                invitation.expires_at = timezone.now()
                invitation.code_digest = ''
                invitation.verified_session = ''
                invitation.delivery = 'revoked'
                invitation.save(update_fields=['expires_at', 'code_digest', 'verified_session', 'delivery'])
        else:
            forget_limits(employee, email, invitation.pk if invitation else None, user_id)
            if invitation:
                invitation.delete()
            if user:
                # Cascades verified email profile, OTP devices and hashed recovery codes.
                # Authored business records keep their snapshots through SET_NULL.
                user.delete()
            employee.user = None
    AccountEvent.objects.create(employee=employee, employee_reference=employee.pk,
                                actor=actor, action=action, reason=reason.strip())
    if action == 'delete_employee':
        employee.delete()
    return ACTIONS[action]
