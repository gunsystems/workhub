from django.contrib import admin
from django.urls import path, include
from django.contrib.auth import views as auth_views
from tasks import views, operations, requests as work_requests
from .health import health
from accounts.views import sign_in
from accounts.logout_views import SafeLogoutView
urlpatterns = [path('work/',include('tasks.workspace_urls')),path('admin/login/',sign_in),path('accounts/',include('accounts.urls')),path('guide/',operations.guide,name='guide'),path('healthz/',health,name='health'),
path('projects/',operations.projects,name='projects'),
path('projects/new/',operations.project_edit,name='project_new'),
path('projects/<int:pk>/',operations.project_detail,name='project_detail'),
path('projects/<int:pk>/edit/',operations.project_edit,name='project_edit'),
path('intake/',work_requests.intake_list,name='intake_list'),
path('intake/new/',work_requests.intake_new,name='intake_new'),
path('intake/<int:pk>/',work_requests.intake_detail,name='intake_detail'),
path('intake/<int:pk>/assign/',work_requests.intake_assign,name='intake_assign'),
path('intake/<int:pk>/action/<str:action>/',work_requests.request_action,name='request_action'),
path('intake/<int:pk>/staffing/',work_requests.staffing_edit,name='request_staffing'),
path('intake/notifications/<int:pk>/read/',work_requests.read_notification,name='read_request_notification'),
path('intake/<int:pk>/edit/',work_requests.intake_edit,name='intake_edit'),
path('intake/<int:pk>/close/',work_requests.intake_close,name='intake_close'),
path('alerts/',operations.alerts,name='alerts'),
path('tasks/<int:pk>/accept/',operations.accept,name='task_accept'),
path('tasks/<int:pk>/checklist/new/',operations.checklist_add,name='checklist_add'),
path('tasks/<int:pk>/checklist/<int:item_pk>/',operations.checklist_update,name='checklist_update'),
path('employees/<int:pk>/feedback/',views.employee_feedback,name='employee_feedback'),path('dashboard/',views.dashboard,name='dashboard'),path('tasks/<int:pk>/actions/<str:action>/',views.task_action,name='task_action'),path('login/',sign_in,name='login'),path('logout/',SafeLogoutView.as_view(),name='logout'),path('admin/',admin.site.urls),path('',views.calendar_view,name='calendar'),path('tasks/new/',views.edit,name='task_new'),path('tasks/<int:pk>/',views.detail,name='task_detail'),path('tasks/<int:pk>/edit/',views.edit,name='task_edit')]
