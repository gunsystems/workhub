from .models import Department, Employee, Task
from django.db.models import Q


def profile(user):
    if not user.is_authenticated or not user.is_active:
        return None
    return Employee.objects.filter(user=user, is_active=True).first()


def is_company_admin(user):
    return user.is_authenticated and user.is_active and user.is_superuser


def can_view_company(user):
    """Business oversight does not grant account administration or approval rights."""
    employee = profile(user)
    return is_company_admin(user) or bool(employee and employee.position in ['head', 'executive'])


def performing_department(department_id):
    return Q(assignee__department_id=department_id) | Q(participants__department_id=department_id)


def performing_employee(employee_id):
    return Q(assignee_id=employee_id) | Q(participants__pk=employee_id)


def visible_employees(user):
    if can_view_company(user):
        return Employee.objects.all()
    employee = profile(user)
    return Employee.objects.filter(department_id=employee.department_id) if employee else Employee.objects.none()


def visible_departments(user):
    if can_view_company(user):
        return Department.objects.all()
    employee = profile(user)
    return Department.objects.filter(pk=employee.department_id) if employee else Department.objects.none()


def visible_tasks(user):
    tasks = Task.objects.all()
    if not can_view_company(user):
        employee = profile(user)
        involved = Q(creator=user) | Q(participants__user=user) | Q(watchers=user) | Q(approval_steps__approver=user)
        team = performing_department(employee.department_id) if employee else Q(pk__in=[])
        tasks = tasks.filter(team | involved) if employee else tasks.none()
    tasks = tasks.exclude(Q(status='draft') & ~Q(creator=user))
    return Task.objects.filter(pk__in=tasks.values('pk')).select_related('assignee__department','assignee__user','project')


def assignable_employees(user):
    if can_view_company(user):
        return Employee.objects.all()
    employee = profile(user)
    if employee and employee.role == 'manager':
        return visible_employees(user).filter(Q(role='employee') | Q(pk=employee.pk)).exclude(user__is_superuser=True)
    return Employee.objects.filter(pk=employee.pk) if employee else Employee.objects.none()


def can_edit(user, task):
    if task.workflow_version == 2:
        return is_company_admin(user) or (user.is_active and task.creator_id == user.pk)
    return assignable_employees(user).filter(pk=task.assignee_id).exists()


def is_manager(user):
    employee = profile(user)
    return is_company_admin(user) or bool(employee and employee.role == 'manager')


def can_review(user, task):
    if task.workflow_version == 2:
        step = task.approval_steps.exclude(decision='approved').first()
        return bool(user.is_active and task.status == 'review' and step and task.approval_steps.filter(stage=step.stage,approver=user).exclude(decision='approved').exists()
                    and not task.participants.filter(user=user).exists())
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
    if task.workflow_version == 2:
        return user.is_active and task.participants.filter(user=user, is_active=True).exists()
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
