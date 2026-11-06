from .models import Department, Employee, Task
from django.db.models import Q


def profile(user):
    if not user.is_authenticated or not user.is_active:
        return None
    return Employee.objects.filter(user=user).first()


def is_company_admin(user):
    return user.is_authenticated and user.is_active and user.is_superuser


def visible_employees(user):
    if is_company_admin(user):
        return Employee.objects.all()
    employee = profile(user)
    return Employee.objects.filter(department_id=employee.department_id) if employee else Employee.objects.none()


def visible_departments(user):
    if is_company_admin(user):
        return Department.objects.all()
    employee = profile(user)
    return Department.objects.filter(pk=employee.department_id) if employee else Department.objects.none()


def visible_tasks(user):
    return Task.objects.filter(assignee__in=visible_employees(user)).select_related('assignee__department','assignee__user','project')


def assignable_employees(user):
    if is_company_admin(user):
        return Employee.objects.all()
    employee = profile(user)
    if employee and employee.role == 'manager':
        return visible_employees(user).filter(Q(role='employee') | Q(pk=employee.pk)).exclude(user__is_superuser=True)
    return Employee.objects.filter(pk=employee.pk) if employee else Employee.objects.none()


def can_edit(user, task):
    return assignable_employees(user).filter(pk=task.assignee_id).exists()


def is_manager(user):
    employee = profile(user)
    return is_company_admin(user) or bool(employee and employee.role == 'manager')


def can_review(user, task):
    return can_manage_employee(user, task.assignee)


def can_manage_employee(user, target):
    if not is_manager(user) or target.user_id == user.pk:
        return False
    if is_company_admin(user):
        return True
    if target.user_id and target.user.is_superuser:
        return False
    employee = profile(user)
    return target.department_id == employee.department_id and target.role == 'employee'


def can_report(user, task):
    return user.is_active and task.assignee.user_id == user.pk


def visible_projects(user):
    from .models import Project
    return Project.objects.filter(department__in=visible_departments(user)).select_related('owner__user','department')


def can_manage_project(user, project):
    return is_company_admin(user) or (is_manager(user) and project.owner.user_id == user.pk)


def visible_requests(user):
    from .models import WorkRequest
    requests = WorkRequest.objects.filter(department__in=visible_departments(user)).select_related('department','project','task')
    return requests if is_manager(user) else requests.filter(creator=user)
