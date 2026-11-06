from django.contrib import admin
from django.urls import path
from django.contrib.auth import views as auth_views
from tasks import views
urlpatterns = [path('login/',auth_views.LoginView.as_view(template_name='tasks/login.html'),name='login'),path('logout/',auth_views.LogoutView.as_view(),name='logout'),path('admin/',admin.site.urls),path('',views.calendar_view,name='calendar'),path('tasks/new/',views.edit,name='task_new'),path('tasks/<int:pk>/',views.detail,name='task_detail'),path('tasks/<int:pk>/edit/',views.edit,name='task_edit')]
