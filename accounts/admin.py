from django import forms
from django.contrib import admin, messages
from django.core.exceptions import ValidationError
from django.urls import reverse
from django.utils.html import format_html
from .models import Invitation, AdministratorAccount
from .services import company_email, invite
from tasks.admin import EmployeeAdmin


class EmployeeInviteForm(forms.ModelForm):
    invitation_email = forms.EmailField(label='초대할 회사 이메일', required=False,
        help_text='새 직원은 저장 시 초대 메일을 보냅니다. 계정이 이미 연결된 직원은 기존 계정을 유지합니다.')

    def clean_invitation_email(self):
        value = self.cleaned_data.get('invitation_email')
        if value:
            value = company_email(value)
            if Invitation.objects.filter(email__iexact=value).exclude(employee_id=self.instance.pk).exists():
                raise ValidationError('다른 직원에게 이미 사용된 이메일입니다.')
        return value

    def clean(self):
        data = super().clean()
        if not self.instance.pk and not data.get('user') and not data.get('invitation_email'):
            self.add_error('invitation_email', '새 직원의 회사 이메일을 입력하세요.')
        if data.get('user') and data.get('invitation_email'):
            self.add_error('invitation_email', '기존 계정 연결과 새 초대는 동시에 할 수 없습니다.')
        return data


def invitation_state(self, obj):
    if not obj.is_active or (obj.user_id and not obj.user.is_active):
        return '사용 중지'
    access = getattr(obj.user, 'workhub_access', None) if obj.user_id else None
    if access and access.otp_reset_key:
        return '이메일 인증 · OTP 재등록 대기'
    invitation = getattr(obj, 'invitation', None)
    if invitation:
        if invitation.used_at and obj.user_id:
            from django_otp import user_has_device
            return '사용 중' if user_has_device(obj.user) else 'OTP 등록 대기'
        return invitation.state
    return '기존 계정' if obj.user_id else '초대 대기'
invitation_state.short_description = '계정 상태'


def save_related(self, request, form, formsets, change):
    super(InvitedEmployeeAdmin, self).save_related(request, form, formsets, change)
    email = form.cleaned_data.get('invitation_email')
    if email:
        try:
            invite(form.instance, email)
        except ValidationError as exc:
            self.message_user(request, ' / '.join(exc.messages), messages.ERROR)
        else:
            self.message_user(request, '초대 메일 발송 요청을 접수했습니다. 수신·인증 여부는 계정 상태에서 확인하세요.')


@admin.action(description='선택 직원 초대 재발송 (이전 링크 무효화)')
def resend(self, request, queryset):
    for employee in queryset:
        invitation = getattr(employee, 'invitation', None)
        if not invitation or employee.user_id:
            self.message_user(request, f'{employee.name}: 초대 이메일이 없거나 기존 계정이 연결되어 있습니다.', messages.WARNING)
            continue
        try:
            invite(employee, invitation.email)
        except ValidationError as exc:
            self.message_user(request, f'{employee.name}: '+ ' / '.join(exc.messages), messages.ERROR)
        else:
            self.message_user(request, f'{employee.name}: 초대 발송 요청을 접수했습니다.')


class InvitedEmployeeAdmin(EmployeeAdmin):
    form = EmployeeInviteForm
    list_display = ['name', 'department', 'user', 'role', 'invitation_state', 'account_management']
    list_filter = ['is_active', 'department', 'role']
    readonly_fields = ['is_active', 'user']
    actions = ['resend']
    invitation_state = invitation_state
    save_related = save_related
    resend = resend

    def has_module_permission(self, request):
        from .lifecycle import system_admin
        return system_admin(request.user)

    def has_view_permission(self, request, obj=None):
        return self.has_module_permission(request)

    def has_add_permission(self, request):
        return self.has_module_permission(request)

    def has_change_permission(self, request, obj=None):
        return self.has_module_permission(request) and not (obj and obj.user_id and obj.user.is_staff and not request.user.is_superuser)

    @admin.display(description='계정 관리')
    def account_management(self, obj):
        return format_html('<a href="{}">OTP 초기화 · 계정 관리</a>', reverse('manage_employee_account', args=[obj.pk]))

    def has_delete_permission(self, request, obj=None):
        # The account lifecycle page removes credentials and checks business history.
        return False


from tasks.models import Employee
admin.site.unregister(Employee)
admin.site.register(Employee, InvitedEmployeeAdmin)

# Ordinary employees are registered by invitation, never by a password-only User form.
from django.contrib.auth import get_user_model
admin.site.unregister(get_user_model())
# Device secrets and direct deletion are not exposed in the general admin UI.
from django_otp.plugins.otp_totp.models import TOTPDevice
if admin.site.is_registered(TOTPDevice):
    admin.site.unregister(TOTPDevice)


@admin.register(AdministratorAccount)
class AdministratorAccountAdmin(admin.ModelAdmin):
    list_display = ['username', 'email', 'is_superuser', 'is_active']
    fields = ['username', 'email', 'is_superuser', 'is_active', 'designation']
    readonly_fields = fields

    def get_queryset(self, request):
        return super().get_queryset(request).filter(is_staff=True)

    @admin.display(description='관리자 지정')
    def designation(self, obj):
        return format_html('<a href="{}">인증된 직원 중 서브 관리자 지정 · 해제</a>', reverse('administrators'))

    def has_module_permission(self, request):
        return request.user.is_superuser

    def has_view_permission(self, request, obj=None):
        return request.user.is_superuser

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    has_delete_permission = has_change_permission
