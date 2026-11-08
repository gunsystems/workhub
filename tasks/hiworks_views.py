from datetime import timedelta
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction, IntegrityError
from django.shortcuts import render, redirect, get_object_or_404
from django.utils import timezone
from .permissions import is_company_admin
from .models import Department, Employee, HiworksLink, HiworksReview, HiworksDelivery
from .hiworks import directory_rows


@login_required
def settings_view(request):
    if not is_company_admin(request.user): raise PermissionDenied
    review = None
    if request.method == 'POST':
        action = request.POST.get('action')
        try:
            if action == 'preview':
                review = HiworksReview.objects.create(owner=request.user, rows=directory_rows())
            elif action == 'apply':
                with transaction.atomic():
                    if not request.POST.get('review','').isdigit(): raise ValidationError('미리보기를 다시 조회해 주세요.')
                    review = get_object_or_404(HiworksReview.objects.select_for_update(), pk=request.POST.get('review'), owner=request.user, applied_at=None, created_at__gte=timezone.now()-timedelta(minutes=15))
                    count = 0
                    for index, row in enumerate(review.rows):
                        if request.POST.get(f'select_{index}') != 'on': continue
                        employee_id = request.POST.get(f'employee_{index}', '')
                        department_id = request.POST.get(f'department_{index}', '')
                        employee = Employee.objects.select_for_update().filter(pk=employee_id).first() if employee_id.isdigit() else None
                        if employee:
                            # Matching an administrator account requires separate account operations.
                            if employee.user_id and employee.user.is_superuser: raise ValidationError('관리자 로그인 계정은 직원 연동 대상으로 변경할 수 없습니다.')
                        else:
                            department = Department.objects.filter(pk=department_id).first() if department_id.isdigit() else None
                            if department_id == 'remote' and row.get('department'):
                                department = Department.objects.get_or_create(name=row['department'])[0]
                            if not department: raise ValidationError('새 직원은 부서를 선택해 주세요.')
                            employee = Employee.objects.create(name=row['name'], department=department)
                        existing = HiworksLink.objects.filter(employee=employee).first()
                        if existing and existing.remote_id != row['remote_id']: raise ValidationError('이미 다른 하이웍스 직원과 연결되어 있습니다.')
                        if HiworksLink.objects.filter(remote_id=row['remote_id']).exclude(employee=employee).exists(): raise ValidationError('다른 직원에 연결된 하이웍스 사용자입니다.')
                        HiworksLink.objects.update_or_create(employee=employee, defaults={**{key:row[key] for key in ['email','remote_id','user_id']}})
                        count += 1
                    if not count: raise ValidationError('반영할 직원을 선택해 주세요.')
                    review.applied_at=timezone.now(); review.save(update_fields=['applied_at'])
                messages.success(request, f'{count}명의 직원 연결을 반영했습니다. 로그인 계정 생성이나 초대 메일 발송은 하지 않았습니다.')
                return redirect('hiworks_settings')
            elif action == 'notification':
                if not request.POST.get('link','').isdigit(): raise ValidationError('연결할 직원을 확인해 주세요.')
                link = get_object_or_404(HiworksLink, pk=request.POST.get('link'))
                if request.POST.get('enabled') == 'on' and not settings.HIWORKS_NOTIFY_TOKEN: raise ValidationError('먼저 메신저 알림 토큰을 설정해 주세요.')
                link.notifications_enabled=request.POST.get('enabled') == 'on'; link.save(update_fields=['notifications_enabled'])
                messages.success(request, '해당 직원의 이후 주요 업무 알림 설정을 저장했습니다.')
                return redirect('hiworks_settings')
            else: raise ValidationError('처리 항목을 확인해 주세요.')
        except (ValidationError, IntegrityError) as exc:
            messages.error(request, exc.messages[0] if isinstance(exc, ValidationError) else '이미 연결된 직원 정보입니다. 중복 선택을 확인해 주세요.')
    if review:
        for row in review.rows:
            row['department_match'] = Department.objects.filter(name=row.get('department','')).first()
            matches=Employee.objects.filter(user__email__iexact=row['email']).exclude(user__is_superuser=True)
            row['suggested']=matches.first().pk if matches.count()==1 else None
    return render(request, 'tasks/hiworks_settings.html', {
        'review':review, 'directory_ready':bool(settings.HIWORKS_DIRECTORY_TOKEN), 'notify_ready':bool(settings.HIWORKS_NOTIFY_TOKEN),
        'employees':Employee.objects.exclude(user__is_superuser=True).select_related('department'), 'departments':Department.objects.all(),
        'links':HiworksLink.objects.select_related('employee__department'),
        'deliveries':HiworksDelivery.objects.select_related('notification__user').order_by('-pk')[:30],
    })
