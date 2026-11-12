from django.contrib.auth.decorators import login_required
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET
from django.core.exceptions import PermissionDenied
from django.shortcuts import render,get_object_or_404
from tasks.models import Employee,StaffEmployment
from tasks.permissions import profile

@login_required
@never_cache
@require_GET
def my_information(request,employee_id=None):
    if employee_id is not None:
        if not request.user.is_superuser:raise PermissionDenied
        employee=get_object_or_404(Employee.objects.select_related('department','user'),pk=employee_id)
    else:employee=profile(request.user)
    employment=StaffEmployment.objects.filter(employee=employee).first() if employee else None
    card=None
    if employee:
        card={'name':employee.name,'department':employee.department.name,
              'job_title':employment.job_title if employment and employment.job_title else employee.get_position_display(),
              'staff_number':employment.staff_number if employment and employment.staff_number else '사번 등록 전'}
    response=render(request,'accounts/my_information.html',{'employee':employee,'employment':employment,'card':card,'admin_preview':employee_id is not None})
    response['Cache-Control']='no-store, private'
    return response
