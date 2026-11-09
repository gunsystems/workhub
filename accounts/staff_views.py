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


class DirectorySelect(forms.Select):
    def __init__(self,*args,blocked=(),**kwargs):
        self.blocked=set(blocked)
        super().__init__(*args,**kwargs)

    def create_option(self,name,value,label,selected,index,subindex=None,attrs=None):
        option=super().create_option(name,value,label,selected,index,subindex,attrs)
        if str(value) in self.blocked: option['attrs']['disabled']=True
        return option


def invitation_directory():
    from django.conf import settings
    from .hiworks_access import fresh_cache, refresh_directory
    from tasks.hiworks import directory_rows
    if settings.WORKHUB_REQUIRE_HIWORKS:
        refresh_directory()
        cache=fresh_cache()
        if not cache:
            raise ValidationError('하이웍스 직원 목록을 불러오지 못했습니다. 하이웍스 자동 연동에서 연결 상태를 확인한 뒤 다시 시도해 주세요.')
        rows=cache.rows
    else:
        rows=directory_rows()
    from django.contrib.auth import get_user_model
    from .models import Invitation, AccessProfile
    from tasks.models import HiworksLink
    registered={email.strip().lower() for email in get_user_model().objects.exclude(email='').values_list('email',flat=True)}
    registered.update(email.lower() for email in AccessProfile.objects.values_list('email',flat=True))
    registered.update(email.lower() for email in HiworksLink.objects.values_list('email',flat=True))
    invitations={item.email.lower():invitation_state(None,item.employee) for item in Invitation.objects.select_related('employee__user')}
    departments={d.name:d.pk for d in Department.objects.all()}
    return [dict(row, state=invitations.get(row['email'].lower()) or ('등록된 계정' if row['email'].lower() in registered else ''),
        department_id=departments.get(row.get('department',''),'')) for row in sorted(rows,key=lambda row:(row['name'],row['email']))]


class StaffForm(EmployeeInviteForm):
    class Meta:
        model=Employee
        fields=['name','department','position','supervisor','role']
    def __init__(self,*args,user=None,directory=None,**kwargs):
        super().__init__(*args,**kwargs)
        self.directory=directory or []
        if not self.instance.pk:
            choices=[('','초대할 직원을 선택해 주세요')]+[(row['remote_id'],f"{row['name']} · {row['email']} · {row.get('department') or '부서 미등록'}"+(f" · {row['state']}" if row['state'] else '')) for row in self.directory]
            self.fields['hiworks_employee']=forms.ChoiceField(label='하이웍스 직원',choices=choices,
                widget=DirectorySelect(blocked=[row['remote_id'] for row in self.directory if row['state']]))
            self.fields['department'].help_text='하이웍스 부서와 같은 이름의 부서가 있으면 자동 선택합니다. 선택되지 않으면 Workhub에서 사용할 부서를 지정해 주세요.'
            self.order_fields(['hiworks_employee','name','invitation_email','department','position','supervisor','role'])
            for field in ['name','invitation_email']:
                self.fields[field].required=False
                self.fields[field].widget.attrs['readonly']=True
            if self.is_bound:
                row=next((row for row in self.directory if row['remote_id']==self.data.get('hiworks_employee') and not row['state']),None)
                self.data=self.data.copy()
                self.data['name']=row['name'] if row else ''
                self.data['invitation_email']=row['email'] if row else ''
        if user and not user.is_superuser:
            self.fields['role'].disabled=True
            self.fields['position'].disabled=True
        self.fields['supervisor'].queryset=Employee.objects.filter(is_active=True).exclude(pk=self.instance.pk)
        if self.instance.user_id:
            self.fields.pop('invitation_email',None)
        self.fields['role'].help_text='업무 조회·배정 역할입니다. 계정 운영용 서브 관리자 지정은 관리자 계정에서 처리합니다.'
        self.fields['position'].help_text='대표·본부장은 전사 업무를 조회할 수 있습니다.'

    def clean_hiworks_employee(self):
        value=self.cleaned_data['hiworks_employee']
        row=next((row for row in self.directory if row['remote_id']==value),None)
        if not row or row['state']:
            raise ValidationError('이미 등록된 직원입니다. 직원 관리에서 기존 직원의 상태를 확인해 주세요.')
        return value


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
        from django.conf import settings
        from .hiworks_access import allowed
        membership = ('하이웍스 등록 확인됨' if allowed(employee.user) else '하이웍스 확인 필요') if settings.WORKHUB_REQUIRE_HIWORKS and employee.user_id and not employee.user.is_superuser else ''
        rows.append({'employee':employee,'email':email,'state':invitation_state(None,employee),'membership':membership,
            'can_edit':not (employee.user_id and (employee.user.is_superuser or (employee.user.is_staff and not request.user.is_superuser))),
            'can_invite':bool(not employee.user_id and employee.is_active and invitation)})
    return render(request,'accounts/staff_list.html',{'rows':rows,'query':query,'department':department,'departments':Department.objects.all()})


@login_required
def employee_edit(request,pk=None):
    ensure_admin(request)
    employee=get_object_or_404(Employee.objects.select_related('user'),pk=pk) if pk else None
    if employee and employee.user_id and (employee.user.is_superuser or (employee.user.is_staff and not request.user.is_superuser)):
        raise PermissionDenied('이 관리자 직원 기록은 이 화면에서 수정할 수 없습니다.')
    directory=[];directory_error=''
    if not employee:
        try: directory=invitation_directory()
        except ValidationError as exc: directory_error=' / '.join(exc.messages)
    form=StaffForm(request.POST if request.method=='POST' else None,instance=employee,user=request.user,directory=directory)
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
    response=render(request,'accounts/staff_form.html',{'form':form,'employee':employee,'directory':directory,'directory_error':directory_error})
    response['Cache-Control']='no-store'
    return response


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
