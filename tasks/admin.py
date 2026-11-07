from django.contrib import admin
from .models import Department, Employee, Task
admin.site.site_header = 'Workhub 관리'
admin.site.register(Department)
@admin.register(Employee)
class EmployeeAdmin(admin.ModelAdmin):
    list_display = ['name','department','user','role']
    list_filter = ['department','role']
    search_fields = ['name','user__username']
@admin.register(Task)
class TaskAdmin(admin.ModelAdmin):
    list_display = ['title','assignee','status','priority','due_date']
    list_filter = ['status','importance','assignee__department']
    def get_readonly_fields(self, request, obj=None):
        return [field.name for field in Task._meta.fields]
    def has_add_permission(self, request): return False
    def has_delete_permission(self, request, obj=None): return False
    def has_change_permission(self, request, obj=None): return False

# Django staff/group flags never grant access around department boundaries.
# Sub-admins can manage employee access; organization/business permissions remain separate.
def company_admin_access(request):
    from accounts.lifecycle import system_admin
    return system_admin(request.user)
admin.site.has_permission = company_admin_access
