"""Workhub-native employee operations with the existing account safeguards."""
from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction, IntegrityError
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
            self.fields['invitation_email'].help_text='선택한 직원의 하이웍스 회사 이메일입니다. 정보만 등록하면 메일을 보내지 않습니다.'
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
        self.can_edit_role=not self.fields['role'].disabled
        self.executive_role=self['position'].value() in ['head','executive']
        if self.executive_role:
            self.fields['role'].disabled=True
        self.fields['role'].choices=[('employee','일반 직원'),('manager','부서 관리자 · 소속 부서 업무 관리')]
        from tasks.hiworks import eligible_auto_employees
        from .lifecycle import account_allowed
        supervisor_ids=[employee.pk for employee in eligible_auto_employees() if account_allowed(employee.user)]
        from .hiworks_access import fresh_cache
        from tasks.models import HiworksLink
        cache=fresh_cache()
        current={(row['remote_id'],row['email'].lower()) for row in (cache.rows if cache else self.directory)}
        supervisor_ids += [link.employee_id for link in HiworksLink.objects.select_related('employee__user').filter(employee__is_active=True)
            if (link.remote_id,link.email.lower()) in current and (not link.employee.user_id or link.employee.user.is_active)]
        self.fields['supervisor'].queryset=Employee.objects.filter(pk__in=supervisor_ids,position__in=['leader','head','executive']).exclude(pk=self.instance.pk).select_related('department').order_by('name','pk')
        self.fields['supervisor'].help_text='하이웍스에 등록된 재직 직원 중 Workhub 직책이 팀장·본부장·대표인 사람만 표시합니다. 먼저 직원 정보를 등록하면 가입 전에도 상위자로 지정할 수 있습니다.'
        if self.instance.pk:
            self.fields.pop('invitation_email',None)
        self.fields['role'].help_text='부서 관리자는 위에서 선택한 소속 부서의 업무를 관리합니다. 다른 부서의 관리 권한이나 시스템 관리자 권한은 부여되지 않습니다.'
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
    from .staff_directory import staff_directory_rows
    directory=[];directory_error=''
    try: directory=invitation_directory()
    except ValidationError as exc: directory_error=' / '.join(exc.messages)
    rows,unlinked=staff_directory_rows(directory,request.user)
    if query:
        value=query.lower()
        rows=[row for row in rows if value in (row['name']+' '+row['email']+' '+(row['employee'].name if row.get('employee') else '')).lower()]
    if department.isdigit():
        rows=[row for row in rows if str(row['employee'].department_id if row.get('employee') else row['department_id'])==department]
    response=render(request,'accounts/staff_list.html',{'rows':rows,'unlinked':unlinked,'query':query,'department':department,
        'departments':Department.objects.all(),'directory_error':directory_error,'total':len(directory)})
    response['Cache-Control']='no-store'
    return response


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
    selected=request.GET.get('hiworks','')
    chosen=next((row for row in directory if row['remote_id']==selected and not row['state']),None)
    initial={'hiworks_employee':selected,'department':chosen['department_id'] if chosen else ''}
    form=StaffForm(request.POST if request.method=='POST' else None,instance=employee,user=request.user,directory=directory,initial=initial)
    if request.method=='POST' and form.is_valid():
        creating=employee is None
        try:
            with transaction.atomic():
                employee=form.save()
                if creating:
                    from .staff_directory import save_directory_link
                    row=next(row for row in directory if row['remote_id']==form.cleaned_data['hiworks_employee'])
                    save_directory_link(employee,row)
        except (ValidationError,IntegrityError):
            messages.error(request,'이미 등록된 직원이거나 연결이 변경되었습니다. 목록을 새로고침해 주세요.')
            return redirect('employee_list')
        email=form.cleaned_data.get('invitation_email') if creating and request.POST.get('action')!='register' else None
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
    link=getattr(employee,'hiworkslink',None)
    if employee.user_id or not (invitation or link): raise PermissionDenied
    email=invitation.email if invitation else link.email
    try: invite(employee,email)
    except ValidationError as exc: messages.error(request,' / '.join(exc.messages))
    else: messages.success(request,'초대 메일 발송을 접수했습니다. 기존 초대가 있었다면 이전 링크는 사용할 수 없습니다.')
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
