from .permissions import can_view_company

def navigation(request):
    if not request.user.is_authenticated: return {}
    path=request.path
    user=request.user
    employee=getattr(user,'employee',None)
    def item(label,href,icon,active=False,key=''):
        return {'label':label,'href':href,'icon':icon,'active':active,'key':key}
    groups=[
        {'label':'내 공간','items':[
            item('내 업무','/dashboard/?scope=mine','home',path=='/dashboard/' and request.GET.get('scope')=='mine'),
            item('개인 보드','/work/personal/','board',path.startswith('/work/personal/'))]},
        {'label':'업무 관리','items':[
            item('업무 현황','/work/','work',path=='/work/' or path.startswith('/tasks/') or (path.startswith('/work/') and not path.startswith(('/work/leave/','/work/personal/','/work/notifications/','/work/hiworks/','/work/reports/','/work/calendar/','/work/system-log/','/work/notices/')))),
            item('업무 보고','/work/reports/','work',path.startswith('/work/reports/')),
            item('간단 업무','/work/quick/','work',path.startswith('/work/quick/')),
            item('프로젝트','/projects/','folder',path.startswith('/projects/')),
            item('업무 접수함','/intake/','inbox',path.startswith('/intake/')),
            item('캘린더','/','calendar',path=='/')]},
        {'label':'알림','items':[
            item('공지사항','/work/notices/','bell',path.startswith('/work/notices/')),
            item('업무 알림','/work/notifications/','bell',path.startswith('/work/notifications/')),
            item('조치 필요','/alerts/','flag',path=='/alerts/',key='alerts')]},
    ]
    if user.is_superuser:
        groups[2]['items'].insert(1,item('설문조사','/work/surveys/','work',path.startswith('/work/surveys/')))
    organization=[]
    from .assessments import executive
    if executive(user):organization.append(item('수행 평가','/work/assessments/','chart',path.startswith('/work/assessments/')))
    if can_view_company(user) or (employee and employee.role=='manager'):
        organization.append(item('전사 현황' if can_view_company(user) else '팀 현황','/dashboard/','chart',path=='/dashboard/' and request.GET.get('scope')!='mine'))
    if organization: groups.append({'label':'조직 현황','items':organization})
    from accounts.lifecycle import system_admin
    administration=[]
    if system_admin(user):
        administration.append(item('직원 관리','/accounts/employees/','users',path.startswith('/accounts/employees/') or path=='/accounts/departments/' or path.startswith('/admin/tasks/employee/') or path.startswith('/work/leave/')))
    if user.is_staff or user.is_superuser:
        administration.append(item('일정 · 업무 휴지통','/work/trash/','folder',path.startswith('/work/trash/')))
    if user.is_superuser:
        administration.append(item('시스템 로그','/work/system-log/','shield',path=='/work/system-log/'))
        administration.append(item('관리자 계정','/accounts/administrators/','shield',path=='/accounts/administrators/'))
        administration.append(item('하이웍스 자동 연동','/work/hiworks/','users',path=='/work/hiworks/'))
        administration.append(item('업무자료 저장소','/work/documents/storage/','folder',path=='/work/documents/storage/'))
    if administration: groups.append({'label':'관리자 전용','items':administration,'collapsible':True,'open':any(i['active'] for i in administration)})
    if path=='/work/documents/storage/':
        for group in groups:
            for entry in group['items']:
                if entry['href']=='/work/':entry['active']=False
    current=next((i['label'] for group in groups for i in group['items'] if i['active']),'업무 공간')
    if path=='/work/register/': current='업무 등록'
    elif path=='/work/calendar/subscription/': current='하이웍스 일정 연결'
    elif path=='/work/calendar/': current='Google Calendar 연결'
    if path.startswith(('/work/surveys/','/work/assessments/','/work/quick/')):
        for group in groups:
            for entry in group['items']:
                if entry['href']=='/work/':entry['active']=False
        current='간단 업무' if path.startswith('/work/quick/') else ('설문조사' if path.startswith('/work/surveys/') else '수행 평가')
    staff_section = ('employment' if path.startswith('/work/leave/settings/') else
                     'leave' if path.startswith('/work/leave/') else
                     'departments' if path == '/accounts/departments/' else 'employees')
    if path.startswith('/work/leave/'):
        current = '직원 관리 · ' + ('직원·연차 설정' if staff_section == 'employment' else '휴가 관리')
    from .version import VERSION
    return {'workhub_version':VERSION,'nav_groups':groups,'nav_current_title':current,'nav_display_name':employee.name if employee else user.get_username(),'staff_management_section':staff_section}
