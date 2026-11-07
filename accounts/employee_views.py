from django import forms
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.core import signing
from django.shortcuts import get_object_or_404, redirect, render
from django.contrib import messages
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods

from tasks.models import Employee
from .lifecycle import ACTIONS, business_counts, change_access, system_admin
from .models import AccountEvent
from .services import digest


class AccountActionForm(forms.Form):
    action = forms.ChoiceField(label='처리 항목', choices=[('', '처리 항목을 선택하세요')] + list(ACTIONS.items()))
    reason = forms.CharField(label='처리 사유', max_length=1000, widget=forms.Textarea(attrs={'rows':3}))


@login_required
@never_cache
@require_http_methods(['GET', 'POST'])
def manage_account(request, employee_id):
    if not system_admin(request.user):
        raise PermissionDenied
    employee = get_object_or_404(Employee.objects.select_related('user','department'), pk=employee_id)
    invitation = getattr(employee, 'invitation', None)
    user = employee.user
    email = (getattr(getattr(user, 'workhub_access', None), 'email', '') or
             (user.email if user else '') or (invitation.email if invitation else ''))
    form = AccountActionForm(request.POST or None)
    counts = business_counts(employee)
    pending_delete = None
    if request.method == 'POST' and form.is_valid():
        try:
            action = form.cleaned_data['action']
            reason = form.cleaned_data['reason']
            confirmation = request.POST.get('confirmation', '')
            identity = email or employee.name
            payload = {'employee':employee.pk, 'user':employee.user_id, 'identity':identity,
                       'actor':request.user.pk, 'session':digest(request.session.session_key or ''),
                       'action':action, 'reason':reason}
            if action == 'delete_employee' and any(counts.values()):
                details = ', '.join(f'{label} {count}건' for label,count in counts.items() if count)
                raise ValidationError(f'{details}이 있어 직원 완전 삭제가 불가능합니다. 퇴사 처리는 사용 중지, 인증 정보만 지우려면 로그인 계정 삭제를 선택하세요.')
            if action in ('delete_access', 'delete_employee'):
                if not confirmation:
                    pending_delete = {
                        'action':action, 'label':ACTIONS[action], 'reason':reason,
                        'token':signing.dumps(payload, salt='employee-delete-confirmation'),
                    }
                else:
                    try:
                        confirmed = signing.loads(confirmation, salt='employee-delete-confirmation', max_age=300)
                    except signing.BadSignature:
                        raise ValidationError('삭제 확인이 만료되었거나 올바르지 않습니다. 다시 처리 항목을 선택해 확인하세요.')
                    if confirmed != payload:
                        raise ValidationError('삭제 대상이나 처리 내용이 변경됐습니다. 다시 확인하세요.')
                    confirmation = identity
            if not pending_delete:
                result = change_access(request.user, employee.pk, action, reason, confirmation)
        except ValidationError as exc:
            form.add_error(None, ' / '.join(exc.messages))
        else:
            if not pending_delete:
                messages.success(request, f'{employee.name}: {result} 완료')
                return redirect('/admin/tasks/employee/')
    return render(request, 'accounts/manage_employee.html', {
        'employee':employee, 'email':email, 'pending_delete':pending_delete,
        'form':form, 'counts':counts, 'has_history':any(counts.values()),
        'events':AccountEvent.objects.filter(employee=employee).select_related('actor')[:10],
        'protected':bool(user and (user.pk == request.user.pk or user.is_superuser or (user.is_staff and not request.user.is_superuser))),
    })
