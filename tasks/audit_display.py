"""Readable administrator presentation; stored audit evidence stays unchanged."""
import json
from django.contrib.auth import get_user_model
from .models import Task, Employee, Project, Department

TYPE_LABELS={'Task':'업무','Employee':'직원','Project':'프로젝트','Department':'부서','Account':'계정',
    'AssignmentMail':'업무 배정 메일','TaskParticipation':'공동 수행','TaskWatchers':'참조자',
    'ApprovalStep':'결재','WorkRequest':'업무 요청','ChecklistItem':'체크리스트','AccountEvent':'계정 처리',
    'PeriodReport':'기간 보고','ReportSubmission':'제출 보고','ReportReview':'보고 검토','ReportFile':'첨부',
    'WorkSeries':'정기 업무','WorkOccurrence':'정기 업무 회차','Notice':'공지사항','NoticeDelivery':'공지 전달'}
KEY_LABELS={'task_id':'업무','employee_id':'직원','user_id':'계정','assignee_id':'담당자',
    'project_id':'프로젝트','department_id':'부서','creator_id':'작성자','approver_id':'결재자',
    'result':'처리 결과','kind':'처리 종류','body':'내용','details':'상세 내용','action':'처리',
    'participants':'수행자','approval_line':'결재라인','private':'비공개 항목','task_title':'업무명',
    'employee_name':'직원명','channel':'전달 방법','status':'상태','recipient_count':'대상 직원 수',
    'progress':'진행률','stage':'결재 단계','decision':'검토 결과','round':'보고 회차','before':'이전 내용','start_date':'시작일','due_date':'마감일','revision':'변경 차수','state':'상태','creator_name':'작성자','audience':'전달 대상','start_time':'시작 시간','timing':'발송 시점'}


def readable(key,value):
    if value is None or value=='None' or value=='': return '없음'
    if key in ['state','status','진행 상태'] and isinstance(value,str):
        value={'draft':'작성 중','scheduled':'발송 대기','published':'공지 게시','todo':'예정','doing':'진행 중',
               'review':'검토 대기','blocked':'보류','done':'완료','cancelled':'취소','pending':'대기','sent':'발송 접수'}.get(value,value)
    if key in ['task_id','employee_id','assignee_id','project_id','department_id','user_id','approver_id','creator_id']:
        try: pk=int(value)
        except (TypeError,ValueError): return str(value)
        if key=='task_id':
            obj=Task.objects.filter(pk=pk).first()
            return ('비공개 업무' if obj.status=='draft' else obj.title) if obj else '삭제된 업무'
        if key in ['employee_id','assignee_id']:
            obj=Employee.objects.filter(pk=pk).first();return obj.name if obj else '삭제된 직원'
        if key in ['project_id','department_id']:
            model=Project if key=='project_id' else Department
            obj=model.objects.filter(pk=pk).first();return obj.name if obj else '삭제된 항목'
        user=get_user_model().objects.filter(pk=pk).first()
        return (getattr(getattr(user,'employee',None),'name',None) or user.get_username()) if user else '삭제된 계정'
    if isinstance(value,bool): return '예' if value else '아니요'
    if isinstance(value,dict):
        return '\n'.join(f'{KEY_LABELS.get(k,k)}: {readable(k,v)}' for k,v in value.items() if k!='id') or '별도 기록 없음'
    if isinstance(value,list): return '\n'.join(readable(key,v) for v in value) or '없음'
    return str(value)


def decorate(event):
    event.target_type_label=TYPE_LABELS.get(event.target_type,'개인 항목' if event.target_type.startswith('Personal') else '시스템 항목')
    before=event.before or {};after=event.after or {}
    event.change_rows=[];event.result_rows=[]
    if event.action=='update' and before and after and not after.get('private'):
        for key in dict.fromkeys([*before,*after]):
            if before.get(key)!=after.get(key):
                event.change_rows.append({'label':KEY_LABELS.get(key,key),'before':readable(key,before.get(key)),
                                          'after':readable(key,after.get(key))})
    else:
        data=before if event.action=='delete' else after
        if not data:
            labels={'login':'로그인했습니다.','logout':'로그아웃했습니다.','session_timeout':'30분 동안 활동이 없어 로그아웃되었습니다.'}
            event.result_rows=[{'label':'처리 결과','value':labels.get(event.action,'처리 내역을 기록했습니다.')}]
        elif data.get('private'):
            event.result_rows=[{'label':'처리 결과','value':'비공개 항목의 변경 사실을 기록했습니다. 내용은 표시하지 않습니다.'}]
        else:
            skip={'id'}
            if data.get('task_title'):skip.add('task_id')
            if data.get('employee_name'):skip.add('employee_id')
            event.result_rows=[{'label':KEY_LABELS.get(k,k),'value':readable(k,v)} for k,v in data.items() if k not in skip]
    if not event.change_rows and not event.result_rows:
        event.result_rows=[{'label':'처리 결과','value':'변경 사실을 기록했습니다.'}]
