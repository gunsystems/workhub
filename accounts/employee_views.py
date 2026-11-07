from django import forms
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.contrib import messages
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods

from tasks.models import Employee
from .lifecycle import ACTIONS, business_counts, change_access
from .models import AccountEvent


class AccountActionForm(forms.Form):
    action = forms.ChoiceField(label='처리 항목', choices=[('', '처리 항목을 선택하세요')] + list(ACTIONS.items()))
    reason = forms.CharField(label='처리 사유', max_length=1000, widget=forms.Textarea(attrs={'rows':3}))
    confirmation = forms.CharField(label='삭제 확인', required=False, max_length=254,
        help_text='삭제하는 경우에만 위에 표시된 회사 이메일 또는 직원 이름을 입력하세요.')


@login_required
@never_cache
@require_http_methods(['GET', 'POST'])
def manage_account(request, employee_id):
    if not request.user.is_superuser:
        raise PermissionDenied
    employee = get_object_or_404(Employee.objects.select_related('user','department'), pk=employee_id)
    invitation = getattr(employee, 'invitation', None)
    user = employee.user
    email = (getattr(getattr(user, 'workhub_access', None), 'email', '') or
             (user.email if user else '') or (invitation.email if invitation else ''))
    form = AccountActionForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        try:
            result = change_access(request.user, employee.pk, **form.cleaned_data)
        except ValidationError as exc:
            form.add_error(None, ' / '.join(exc.messages))
        else:
            messages.success(request, f'{employee.name}: {result} 완료')
            return redirect('/admin/tasks/employee/')
    return render(request, 'accounts/manage_employee.html', {
        'employee':employee, 'email':email, 'confirmation_text':email or employee.name,
        'form':form, 'counts':business_counts(employee),
        'events':AccountEvent.objects.filter(employee=employee).select_related('actor')[:10],
        'protected':bool(user and (user.pk == request.user.pk or user.is_superuser)),
    })
