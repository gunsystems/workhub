from datetime import timedelta
from django.core.management.base import BaseCommand
from django.utils import timezone
from tasks.models import Department, Employee, Task
class Command(BaseCommand):
    help = '빈 업무 DB에만 예시 직원과 업무를 만듭니다. 실제 직원 정보가 아닙니다.'
    def handle(self,*args,**kwargs):
        if Task.objects.exists():
            self.stdout.write('기존 업무가 있어 예시 생성을 건너뜁니다.')
            return
        today=timezone.localdate()
        names=[('개발팀','김민준'),('영업팀','이서연'),('경영지원팀','박지훈'),('기술지원팀','정수빈')]
        people=[]
        for dept,name in names:
            d,_=Department.objects.get_or_create(name=dept)
            e,_=Employee.objects.get_or_create(name=f'{name} (예시)',department=d)
            people.append(e)
        rows=[('고객사 보안 솔루션 제안서',1,-3,-1,'doing',1,'critical'),('Workhub 캘린더 화면 구현',0,-1,2,'doing',1,'high'),('11월 팀 운영 계획 정리',2,0,0,'todo',2,'high'),('고객 기술 문의 대응',3,0,1,'doing',2,'normal'),('서비스 정기 점검',3,3,5,'todo',3,'normal'),('신규 고객 미팅 준비',1,4,6,'todo',2,'high'),('월간 업무 보고서 작성',2,7,9,'todo',3,'normal'),('API 연동 검토',0,-4,-2,'done',3,'normal'),('시스템 개선 요구사항 정리',0,1,3,'blocked',2,'high')]
        for title,person,start,end,status,priority,importance in rows:
            Task.objects.create(title='[예시] '+title,description='기능 확인을 위한 예시 업무입니다. 실제 업무로 수정하거나 관리자에서 삭제하세요.',assignee=people[person],start_date=today+timedelta(days=start),due_date=today+timedelta(days=end),status=status,priority=priority,importance=importance)
        self.stdout.write('예시 업무 9건 생성 완료')
