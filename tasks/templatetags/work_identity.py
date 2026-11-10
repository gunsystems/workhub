from django import template
register = template.Library()

@register.filter
def work_identity(user, fallback=''):
    if user:
        employee = getattr(user, 'employee', None)
        if employee:
            return f'{employee.department} · {employee.name}'
    return fallback or (user.get_username() if user else '시스템')
