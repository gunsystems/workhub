from django import template
register = template.Library()

@register.filter
def work_identity(user, fallback=''):
    if user:
        employee = getattr(user, 'employee', None)
        if employee:
            return f'{employee.department} · {employee.name}'
    return fallback or (user.get_username() if user else '시스템')

@register.filter
def elapsed_since(value):
    from django.utils import timezone
    if not value: return '배정 시각 기록 없음'
    minutes = max(0, int((timezone.now() - value).total_seconds() // 60))
    days, rest = divmod(minutes, 1440)
    hours, mins = divmod(rest, 60)
    if days: return f'{days}일 {hours}시간 경과'
    if hours: return f'{hours}시간 {mins}분 경과'
    return f'{minutes}분 경과'

@register.filter
def next_progress(task,user):
    from tasks.progress_reporting import next_report_number
    return next_report_number(task,user)
