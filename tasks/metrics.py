"""Transparent v1 scores: original-deadline cohort, never inferred legacy completions."""
from django.utils import timezone


def employee_metrics(employee, tasks, start, end):
    today = timezone.localdate()
    tasks = [t for t in tasks if t.assignee_id == employee.pk]
    active = [t for t in tasks if t.status != 'done']
    cohort = [t for t in tasks if start <= (t.original_due_date or t.due_date) <= end]
    # Awaiting manager review and unknown legacy completions are not penalized.
    pending = [t for t in cohort if t.status == 'review']
    legacy = [t for t in cohort if t.status == 'done' and not t.completed_at]
    eligible = [t for t in cohort if t not in pending and t not in legacy and ((t.original_due_date or t.due_date) < today or t.completed_at)]
    completed = [t for t in eligible if t.status == 'done' and t.completed_at]
    on_time = [t for t in completed if timezone.localtime(t.completed_at).date() <= (t.original_due_date or t.due_date)]
    quality_tasks = [t for t in completed if t.quality_score is not None]
    completion = round(len(completed)/len(eligible)*100,1) if eligible else None
    timeliness = round(len(on_time)/len(eligible)*100,1) if eligible else None
    quality = round(sum(t.quality_score for t in quality_tasks)/len(quality_tasks),1) if quality_tasks else None
    total = round(timeliness*.4+completion*.3+quality*.3,1) if quality is not None and eligible else None
    late = [t for t in cohort if (t.completed_at and timezone.localtime(t.completed_at).date() > (t.original_due_date or t.due_date)) or (t.status not in ['done','review'] and (t.original_due_date or t.due_date) < today)]
    overdue = [t for t in active if t.deadline == 'overdue' and t.status != 'review']
    stale = [t for t in active if t.needs_report]
    not_started = [t for t in active if t.not_started]
    report_gaps = sum(1 for t in tasks for event in t.events.all() if event.kind in ['progress','submitted'] and event.snapshot.get('report_gap_days',0) >= 3 and start <= timezone.localtime(event.created_at).date() <= end)
    reasons = []
    if report_gaps >= 2: reasons.append(f'기간 내 보고 공백 {report_gaps}회')
    if len(late) >= 2: reasons.append(f'기간 내 지연 {len(late)}건')
    if len(stale) >= 2: reasons.append(f'장기 미보고 {len(stale)}건')
    if len(not_started) >= 2: reasons.append(f'미착수 {len(not_started)}건')
    return {'employee':employee,'active':len(active),'overdue':len(overdue),'stale':len(stale),'not_started':len(not_started),'pending':len(pending),'legacy':len(legacy),'eligible':len(eligible),'completed':len(completed),'on_time_count':len(on_time),'quality_count':len(quality_tasks),'completion':completion,'timeliness':timeliness,'quality':quality,'score':total,'late':len(late),'report_gaps':report_gaps,'reasons':reasons,'attention':bool(reasons)}
