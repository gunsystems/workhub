"""Workhub-native employee operations with the existing account safeguards."""
from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Q
from django.shortcuts import render, redirect, get_object_or_404
from django.views.decorators.http import require_POST
from tasks.models import Employee, Department
from .lifecycle import system_admin
from .admin import EmployeeInviteForm, invitation_state
from .services import invite


class StaffForm(EmployeeInviteForm):
    class Meta:
        model=Employee
        fields=['name','department','position','supervisor','role']
    def __init__(self,*args,user=None,**kwargs):
        super().__init__(*args,**kwargs)
        if user and not user.is_superuser:
            self.fields['role'].disabled=True
            self.fields['position'].disabled=True
        self.fields['supervisor'].queryset=Employee.objects.filter(is_active=True).exclude(pk=self.instance.pk)
        if self.instance.user_id:
            self.fields.pop('invitation_email',None)
        self.fields['role'].help_text='업무 조회·배정 역할입니다. 계정 운영용 서브 관리자 지정은 관리자 계정에서 처리합니다.'
        self.fields['position'].help_text='대표·본부장은 전사 업무를 조회할 수 있습니다.'


def ensure_admin(request):
    if not system_admin(request.user): raise PermissionDenied


@login_required
def employee_list(request):
    ensure_admin(request)
    query=request.GET.get('q','').strip()[:100]
    department=request.GET.get('department','')
    employees=Employee.objects.select_related('department','user__workhub_access','invitation').order_by('-is_active','name','pk')
    if query: employees=employees.filter(Q(name__icontains=query)|Q(user__email__icontains=query)|Q(invitation__email__icontains=query))
    if department.isdigit(): employees=employees.filter(department_id=department)
    rows=[]
    for employee in employees[:300]:
        invitation=getattr(employee,'invitation',None)
        email=(employee.user.email if employee.user_id else '') or (invitation.email if invitation else '')
        rows.append({'employee':employee,'email':email,'state':invitation_state(None,employee),
            'can_edit':not (employee.user_id and (employee.user.is_superuser or (employee.user.is_staff and not request.user.is_superuser))),
            'can_invite':bool(not employee.user_id and employee.is_active and invitation)})
    return render(request,'accounts/staff_list.html',{'rows':rows,'query':query,'department':department,'departments':Department.objects.all()})


@login_required
def employee_edit(request,pk=None):
    ensure_admin(request)
    employee=get_object_or_404(Employee.objects.select_related('user'),pk=pk) if pk else None
    if employee and employee.user_id and (employee.user.is_superuser or (employee.user.is_staff and not request.user.is_superuser)):
        raise PermissionDenied('이 관리자 직원 기록은 이 화면에서 수정할 수 없습니다.')
    form=StaffForm(request.POST if request.method=='POST' else None,instance=employee,user=request.user)
    if request.method=='POST' and form.is_valid():
        # Invitation delivery failures leave a visible employee record for retry, like the original admin.
        with transaction.atomic(): employee=form.save()
        email=form.cleaned_data.get('invitation_email')
        if email:
            try: invite(employee,email)
            except ValidationError as exc: messages.error(request,'직원 기록은 저장됐지만 초대 발송을 확인해 주세요: '+' / '.join(exc.messages))
            else: messages.success(request,'직원을 등록하고 초대 메일 발송을 접수했습니다. 가입 완료 후 하이웍스가 자동 연결됩니다.')
        else: messages.success(request,'직원 정보를 저장했습니다.')
        return redirect('employee_list')
    return render(request,'accounts/staff_form.html',{'form':form,'employee':employee})


@login_required
@require_POST
def resend_invitation(request,pk):
    ensure_admin(request)
    employee=get_object_or_404(Employee,pk=pk)
    invitation=getattr(employee,'invitation',None)
    if employee.user_id or not invitation: raise PermissionDenied
    try: invite(employee,invitation.email)
    except ValidationError as exc: messages.error(request,' / '.join(exc.messages))
    else: messages.success(request,'초대 메일 발송을 접수했습니다. 이전 초대 링크는 사용할 수 없습니다.')
    return redirect('employee_list')


@login_required
def departments(request):
    ensure_admin(request)
    if not request.user.is_superuser: raise PermissionDenied
    class DepartmentForm(forms.ModelForm):
        class Meta:
            model=Department
            fields=['name']
    form=DepartmentForm(request.POST or None)
    if request.method=='POST' and form.is_valid():
        form.save();messages.success(request,'부서를 추가했습니다.');return redirect('department_management')
    return render(request,'accounts/departments.html',{'form':form,'departments':Department.objects.all()})
