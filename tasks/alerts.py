from django.urls import reverse
from django.utils import timezone
from django.db.models import Q
from .permissions import visible_tasks, visible_requests, can_review, is_manager, profile, is_company_admin,can_view_company


def action_alerts(user):
    if not user.is_authenticated or not user.is_active:
        return []
    employee = profile(user)
    managing = is_manager(user) or can_view_company(user)
    alerts = []
    seen = set()
    tasks = visible_tasks(user)
    if not managing:
        tasks = tasks.filter(Q(assignee__user=user)|Q(participants__user=user)|Q(approval_steps__approver=user)).distinct()
    def add(key,title,detail,url,level=1):
        if key in seen: return
        seen.add(key)
        alerts.append({'key':key,'title':title,'detail':detail,'url':url,'level':level})
    for task in tasks:
        own = task.assignee.user_id == user.pk
        label = f'{task.title} · {task.assignee.name}'
        url = reverse('task_detail',args=[task.pk])
        if task.awaiting_acceptance and (own or managing):
            add(f'accept:{task.pk}','배정 확인 필요',label,url,2)
        if task.status in ['todo','doing','blocked']:
            if task.deadline == 'overdue': add(f'late:{task.pk}','기한 초과',label,url,3)
            elif task.deadline == 'soon': add(f'soon:{task.pk}','3일 이내 마감',label,url)
            if task.needs_report: add(f'report:{task.pk}','진행 보고 필요',label,url,3)
            if managing and not task.assignee.user_id:
                add(f'account:{task.assignee_id}','직원 로그인 계정 연결 필요',label,reverse('admin:tasks_employee_change',args=[task.assignee_id]) if is_company_admin(user) else url,2)
        if can_review(user,task) and task.status == 'review':
            add(f'review:{task.pk}','검토 기한 초과' if task.review_overdue else '완료 검토 요청',label,url,3 if task.review_overdue else 2)
    if managing:
        for intake in visible_requests(user).filter(task__isnull=True,closed_at__isnull=True):
            waiting = (timezone.now()-intake.created_at).days >= 2
            add(f'intake:{intake.pk}','미배정 요청 지연' if waiting or intake.overdue else '업무 배정 필요',intake.title,reverse('intake_detail',args=[intake.pk]),3 if waiting or intake.overdue else 2)
    from .models import PersonalCard,PersonalReminder
    clock=timezone.localtime()
    for card in PersonalCard.objects.filter(column__board__owner=user,state='active').filter(Q(due_date__lt=clock.date())|Q(due_date=clock.date(),due_time__isnull=True)|Q(due_date=clock.date(),due_time__lte=clock.time().replace(tzinfo=None))):
        add(f'personal:{card.pk}','개인 카드 확인 · 나만 보기',card.title,reverse('personal_card_edit',args=[card.pk]),1)
    for reminder in PersonalReminder.objects.filter(card__column__board__owner=user,card__state='active',delivered_at__isnull=False,read_at__isnull=True).select_related('card'):
        add(f'personal:{reminder.card_id}','개인 일정 알림 · 나만 보기',reminder.card.title,reverse('personal_card_edit',args=[reminder.card_id]),1)
    from .models import WorkSeries,PeriodReport
    for rule in WorkSeries.objects.filter(enabled=True,template_task__in=visible_tasks(user)).exclude(issue=''):
        if managing or rule.owner_id==user.pk:
            add(f'series:{rule.pk}','정기 업무 인계 · 설정 확인',rule.issue,reverse('work_series',args=[rule.template_task_id]),3)
    for report in PeriodReport.objects.filter(reviewer=user,state='submitted'):
        add(f'period-report:{report.pk}','기간 보고 검토 대기',f'{report.period_start}~{report.period_end}',reverse('period_report_detail',args=[report.pk]),2)
    return sorted(alerts,key=lambda a:(-a['level'],a['key']))


def alert_context(request):
    return {'action_alert_count':len(action_alerts(request.user))}
