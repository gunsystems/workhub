from django.urls import path
from . import views
from .employee_views import manage_account
from .reset_views import restore
from .administrator_views import administrators

urlpatterns = [
    path('password/reset-preview/', views.password_reset_preview, name='password_reset_preview'),
    path('administrators/', administrators, name='administrators'),
    path('employees/<int:employee_id>/manage/', manage_account, name='manage_employee_account'),
    path('invitation-preview/<int:employee_id>/', views.invitation_preview, name='invitation_preview'),
    path('activate/<str:token>/', views.activate, name='activate'),
    path('mfa/setup/', views.mfa_setup, name='mfa_setup'),
    path('mfa/restore/', restore, name='mfa_restore'),
    path('mfa/verify/', views.mfa_verify, name='mfa_verify'),
    path('mfa/recovery/', views.recovery, name='mfa_recovery'),
    path('password/reset/', views.ResetPassword.as_view(), name='password_reset'),
    path('password/reset/sent/', lambda request: views.page(request, '비밀번호 재설정 안내', notice='사용 가능한 인증 계정이면 재설정 메일을 발송합니다. 메일함을 확인하세요.'), name='password_reset_done'),
    path('password/reset/<uidb64>/<token>/', views.ConfirmResetPassword.as_view(), name='password_reset_confirm'),
]
