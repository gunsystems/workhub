"""Remove only unchanged seed_demo fixtures after an explicit reviewed operation."""
import json
import os
from pathlib import Path
from django.conf import settings
from django.core import serializers
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone
from tasks.models import Employee, Task, TaskEvent, HiworksLink, CalendarExport, WorkNotification, WorkRequest
from accounts.models import Invitation, AccountEvent

NAMES=['김민준 (예시)','이서연 (예시)','박지훈 (예시)','정수빈 (예시)']
TITLES=['고객사 보안 솔루션 제안서','Workhub 캘린더 화면 구현','11월 팀 운영 계획 정리','고객 기술 문의 대응','서비스 정기 점검','신규 고객 미팅 준비','월간 업무 보고서 작성','API 연동 검토','시스템 개선 요구사항 정리']
DESCRIPTION='기능 확인을 위한 예시 업무입니다. 실제 업무로 수정하거나 관리자에서 삭제하세요.'


def plan(include_feedback=False):
    employees=list(Employee.objects.filter(name__in=NAMES).order_by('pk'))
    if not employees: return [],[]
    if len(employees)!=4 or any(employee.user_id for employee in employees): raise CommandError('예시 직원이 변경되어 자동 정리를 중단합니다.')
    ids=[employee.pk for employee in employees]
    if (HiworksLink.objects.filter(employee_id__in=ids).exists() or Invitation.objects.filter(employee_id__in=ids).exists()
            or AccountEvent.objects.filter(employee_id__in=ids).exists() or Employee.objects.filter(supervisor_id__in=ids).exists()):
        raise CommandError('예시 직원에 실제 연결/운영 이력이 있어 정리를 중단합니다.')
    from accounts.lifecycle import business_counts
    if any(count for employee in employees for label,count in business_counts(employee).items() if label!='담당 업무'):
        raise CommandError('예시 직원에 업무 이력이 있어 정리를 중단합니다.')
    tasks=list(Task.objects.filter(assignee_id__in=ids).order_by('pk'))
    if len(tasks)!=9 or {task.title for task in tasks}!={'[예시] '+title for title in TITLES}:
        raise CommandError('초기 예시 업무 구성이 변경되어 정리를 중단합니다.')
    for task in tasks:
        if task.description!=DESCRIPTION or task.creator_id or task.project_id or task.workflow_version!=1:
            raise CommandError('예시 업무가 실제 업무로 수정되어 정리를 중단합니다.')
        if task.events.exclude(kind='feedback').exists() or (not include_feedback and task.events.exists()):
            raise CommandError('예시 업무에 추가 이력이 있어 정리를 중단합니다. 피드백까지 검토한 경우에만 --include-feedback을 사용하세요.')
        for relation in ['checklist','assignments','approval_steps','participants','watchers']:
            if getattr(task,relation).exists(): raise CommandError('예시 업무에 추가 이력이 있어 정리를 중단합니다.')
    if CalendarExport.objects.filter(task__in=tasks).exists(): raise CommandError('외부 캘린더 연결 업무가 있어 정리를 중단합니다.')
    if WorkRequest.objects.filter(task__in=tasks).exists(): raise CommandError('접수 업무 연결이 있어 정리를 중단합니다.')
    return employees,tasks


class Command(BaseCommand):
    help='초기 예시 직원 4명과 수정되지 않은 예시 업무 9건만 정리합니다. 기본 동작은 조회입니다.'
    def add_arguments(self,parser):
        parser.add_argument('--apply',action='store_true')
        parser.add_argument('--include-feedback',action='store_true')

    def handle(self,*args,**options):
        employees,tasks=plan(options['include_feedback'])
        self.stdout.write(json.dumps({'employees':[{'id':employee.pk,'name':employee.name} for employee in employees],
            'tasks':[{'id':task.pk,'title':task.title} for task in tasks],
            'feedback':TaskEvent.objects.filter(task__in=tasks).count(),
            'notifications':WorkNotification.objects.filter(task__in=tasks).count(),'apply':options['apply']},ensure_ascii=False))
        if not options['apply'] or not employees: return
        with transaction.atomic():
            employees,tasks=plan(options['include_feedback'])
            # A private backup of only these fixture rows; never export authentication data.
            directory=Path(settings.DATABASES['default']['NAME']).resolve().parent
            path=directory/('workhub-demo-backup-'+timezone.now().strftime('%Y%m%d%H%M%S%f')+'.json')
            data=serializers.serialize('json',employees+tasks+list(TaskEvent.objects.filter(task__in=tasks)))
            fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
            with os.fdopen(fd,'w',encoding='utf-8') as stream: stream.write(data)
            Task.objects.filter(pk__in=[task.pk for task in tasks]).delete()
            Employee.objects.filter(pk__in=[employee.pk for employee in employees]).delete()
        self.stdout.write('예시 직원 4명 · 예시 업무 9건 정리 완료. 실제 계정과 부서는 유지했습니다.')
