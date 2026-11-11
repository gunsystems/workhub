"""Calendar-derived drafts never claim that scheduled work was performed."""
from datetime import timedelta
from django.utils import timezone
from django.db.models import Q
from .models import Task,PeriodReport,Employee
from .permissions import profile,performing_employee,can_view_company

def week_start(day):
    if not 1900<=day.year<=2100:day=timezone.localdate()
    return day-timedelta(days=day.weekday())

def responsible_reviewer(employee):
    if not employee:return None
    positions=['leader'] if employee.position=='member' else (['head','executive'] if employee.position=='leader' else ['executive'])
    for position in positions:
        qs=Employee.objects.filter(is_active=True,position=position,user__is_active=True).exclude(pk=employee.pk)
        if position=='leader':qs=qs.filter(department_id=employee.department_id)
        ids=list(qs.values_list('user_id',flat=True)[:2])
        if len(ids)==1:return ids[0]
        if len(ids)>1:return None
    return None

def calendar_draft(user,start):
    employee=profile(user)
    if not employee:return {'current':'','next':'','task_ids':[],'count':0,'weekend':False}
    end=start+timedelta(days=13)
    # Date-only and timed events follow the same interval rules as Workhub's calendar.
    qs=Task.objects.filter(performing_employee(employee.pk)).exclude(status__in=['draft','cancelled']).distinct().select_related('project','category')
    qs=qs.filter((Q(activity_start__date__lte=end)&(Q(activity_end__date__gte=start)|Q(activity_end__isnull=True,due_date__gte=start)))|Q(activity_start__isnull=True,start_date__lte=end,due_date__gte=start))
    current=[];upcoming=[];ids=[];weekend=False
    for task in qs:
        first=timezone.localtime(task.activity_start).date() if task.activity_start else task.start_date
        last=timezone.localtime(task.activity_end-timedelta(microseconds=1)).date() if task.activity_end else task.due_date
        if last<start or first>end:continue
        ids.append(task.pk)
        for bucket,begin,finish in [(current,start,start+timedelta(days=6)),(upcoming,start+timedelta(days=7),end)]:
            lo=max(first,begin);hi=min(last,finish)
            if lo>hi:continue
            label=lo.strftime('%m.%d') if lo==hi else lo.strftime('%m.%d')+'~'+hi.strftime('%m.%d')
            project=' · '+task.project.name if task.project_id else ''
            category=task.category.name if task.category_id else '일반 업무'
            title=task.title if task.title.startswith('['+category+']') else '['+category+'] '+task.title
            text=f'- [{label}] {title}{project} ({task.get_status_display()} · {task.progress}%)'
            bucket.append((lo,task.pk,text))
            if bucket is current and any((lo+timedelta(days=n)).weekday()>=5 for n in range((hi-lo).days+1)):weekend=True
    return {'current':'\n'.join(row[2] for row in sorted(current)), 'next':'\n'.join(row[2] for row in sorted(upcoming)), 'task_ids':ids,'count':len(current),'weekend':weekend}

def submission_board(user,start):
    employee=profile(user)
    if not (can_view_company(user) or (employee and (employee.position=='leader' or employee.role=='manager'))):return []
    staff=Employee.objects.filter(is_active=True,user__is_active=True,position__in=['member','leader']).select_related('user','department')
    if not can_view_company(user):staff=staff.filter(department_id=employee.department_id)
    staff=staff.exclude(user=user)
    reports=PeriodReport.objects.filter(report_type='weekly',period_start=start,period_end=start+timedelta(days=6)).exclude(state='draft').select_related('author','current_submission').order_by('-submitted_at','-pk')
    if not can_view_company(user):reports=reports.filter(author__employee__department_id=employee.department_id)
    latest={}
    for report in reports:latest.setdefault(report.author_id,report)
    return [{'employee':person,'report':latest.get(person.user_id),'label':({'submitted':'제출 완료','returned':'보완 요청','approved':'확인 완료'}.get(latest[person.user_id].state,'미제출') if person.user_id in latest else '미제출')} for person in staff.order_by('department__name','name')]
