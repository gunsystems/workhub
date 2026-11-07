from django import forms
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.shortcuts import redirect, render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods
from django_otp import user_has_device

from tasks.models import Employee
from .models import AccountEvent


class AdministratorForm(forms.Form):
    employee = forms.ModelChoiceField(label='직원', queryset=Employee.objects.none())
    action = forms.ChoiceField(label='처리', choices=[('grant_admin','서브 관리자 지정'),('revoke_admin','서브 관리자 해제')])
    reason = forms.CharField(label='처리 사유', max_length=1000)


@login_required
@never_cache
@require_http_methods(['GET', 'POST'])
def administrators(request):
    if not request.user.is_superuser:
        raise PermissionDenied
    form = AdministratorForm(request.POST or None)
    form.fields['employee'].queryset = Employee.objects.filter(is_active=True, user__is_active=True, user__is_superuser=False).select_related('user','department')
    if request.method == 'POST' and form.is_valid():
        with transaction.atomic():
            employee = Employee.objects.select_for_update().get(pk=form.cleaned_data['employee'].pk)
            user = get_user_model().objects.select_for_update().get(pk=employee.user_id)
            access = getattr(user, 'workhub_access', None)
            grant = form.cleaned_data['action'] == 'grant_admin'
            if user.is_superuser or not employee.is_active or not user.is_active:
                form.add_error(None, '사용 가능한 일반 직원 계정만 지정할 수 있습니다.')
            elif grant and not (access and access.verified_at and not access.otp_reset_key and user_has_device(user)):
                form.add_error(None, '회사 이메일 인증과 OTP 등록을 먼저 완료해야 합니다.')
            else:
                user.is_staff = grant
                user.save(update_fields=['is_staff'])
                from .lifecycle import revoke_sessions
                revoke_sessions(user.pk)
                AccountEvent.objects.create(employee=employee, employee_reference=employee.pk, actor=request.user,
                                            action=form.cleaned_data['action'], reason=form.cleaned_data['reason'])
                messages.success(request, '관리자 권한을 변경했습니다. 해당 직원은 다시 로그인해야 합니다.')
                return redirect('administrators')
    return render(request, 'accounts/administrators.html', {'form':form,
        'administrators':get_user_model().objects.filter(is_staff=True).order_by('-is_superuser','username')})
