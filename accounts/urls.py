from . import access_views
from . import staff_views
from django.urls import path
from . import views
from .employee_views import manage_account
from .reset_views import restore
from .administrator_views import administrators
from . import idle
from . import profile_views

urlpatterns = [
    path('me/', profile_views.my_information, name='my_information'),
    path('employees/<int:employee_id>/profile/', profile_views.my_information, name='employee_information'),
    path('session/activity/', idle.activity, name='session_activity'),
    path('session/status/', idle.status, name='session_status'),
    path('hiworks/access/', access_views.retry, name='hiworks_access_retry'),
    path('employees/', staff_views.employee_list, name='employee_list'),
    path('employees/new/', staff_views.employee_edit, name='employee_new'),
    path('employees/<int:pk>/edit/', staff_views.employee_edit, name='employee_edit'),
    path('employees/<int:pk>/resend/', staff_views.resend_invitation, name='employee_resend'),
    path('departments/', staff_views.departments, name='department_management'),
    path('password/reset-preview/', views.password_reset_preview, name='password_reset_preview'),
    path('administrators/', administrators, name='administrators'),
    path('employees/<int:employee_id>/manage/', manage_account, name='manage_employee_account'),
    path('invitation-preview/<int:employee_id>/', views.invitation_preview, name='invitation_preview'),
    path('activate/<str:token>/', views.activate, name='activate'),
    path('mfa/switch/', views.switch_account, name='switch_account'),
    path('mfa/setup/', views.mfa_setup, name='mfa_setup'),
    path('mfa/restore/', restore, name='mfa_restore'),
    path('mfa/verify/', views.mfa_verify, name='mfa_verify'),
    path('mfa/recovery/', views.recovery, name='mfa_recovery'),
    path('password/reset/', views.ResetPassword.as_view(), name='password_reset'),
    path('password/reset/sent/', lambda request: views.page(request, '비밀번호 재설정 안내', notice='사용 가능한 인증 계정이면 재설정 메일을 발송합니다. 메일함을 확인하세요.'), name='password_reset_done'),
    path('password/reset/<uidb64>/<token>/', views.ConfirmResetPassword.as_view(), name='password_reset_confirm'),
]
