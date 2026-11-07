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
            item('공식 업무','/work/','work',path=='/work/' or path.startswith('/tasks/') or (path.startswith('/work/') and not path.startswith(('/work/personal/','/work/notifications/')))),
            item('프로젝트','/projects/','folder',path.startswith('/projects/')),
            item('업무 접수함','/intake/','inbox',path.startswith('/intake/')),
            item('캘린더','/','calendar',path=='/')]},
        {'label':'알림','items':[
            item('업무 알림','/work/notifications/','bell',path.startswith('/work/notifications/')),
            item('조치 필요','/alerts/','flag',path=='/alerts/',key='alerts')]},
    ]
    organization=[]
    if user.is_superuser or (employee and employee.role=='manager'):
        organization.append(item('전사 현황' if user.is_superuser else '팀 현황','/dashboard/','chart',path=='/dashboard/' and request.GET.get('scope')!='mine'))
    if user.is_staff: organization.append(item('직원 관리','/admin/tasks/employee/','users',path.startswith('/admin/tasks/employee/')))
    if user.is_superuser: organization.append(item('관리자 계정','/accounts/administrators/','shield',path=='/accounts/administrators/'))
    if organization: groups.append({'label':'조직 관리','items':organization,'collapsible':True,'open':any(i['active'] for i in organization)})
    current=next((i['label'] for group in groups for i in group['items'] if i['active']),'업무 공간')
    return {'nav_groups':groups,'nav_current_title':current,'nav_display_name':employee.name if employee else user.get_username()}
