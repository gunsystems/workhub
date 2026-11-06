from django.contrib import admin
from .models import Department, Employee, Task
admin.site.site_header = 'Workhub 관리'
admin.site.register(Department)
admin.site.register(Employee)
@admin.register(Task)
class TaskAdmin(admin.ModelAdmin):
    list_display = ['title','assignee','status','priority','due_date']
    list_filter = ['status','importance','assignee__department']
