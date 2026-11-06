from .models import Department, Employee, Task


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
    return Task.objects.filter(assignee__in=visible_employees(user)).select_related('assignee__department')


def assignable_employees(user):
    if is_company_admin(user):
        return Employee.objects.all()
    employee = profile(user)
    if employee and employee.role == 'manager':
        return visible_employees(user)
    return Employee.objects.filter(pk=employee.pk) if employee else Employee.objects.none()


def can_edit(user, task):
    return assignable_employees(user).filter(pk=task.assignee_id).exists()
