from datetime import date
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


class WorkspaceMigrationTests(TransactionTestCase):
    def test_board_design_migration_keeps_existing_cards_and_checklists(self):
        executor=MigrationExecutor(connection)
        executor.migrate([('tasks','0012_personalcard_repeat_day_task_review_started_at')])
        old=executor.loader.project_state([('tasks','0012_personalcard_repeat_day_task_review_started_at')]).apps
        try:
            owner=old.get_model('auth','User').objects.create(username='board-migration-owner')
            board=old.get_model('tasks','PersonalBoard').objects.create(owner=owner,name='기존 개인 보드')
            column=old.get_model('tasks','PersonalColumn').objects.create(board=board,name='완료')
            card=old.get_model('tasks','PersonalCard').objects.create(column=column,title='기존 카드',body='보존 메모',
                state='active',checklist=[{'text':'기존 체크','done':True}])
        finally:
            MigrationExecutor(connection).migrate([('tasks','0013_personalcard_priority_personalcolumn_color_and_more')])
        from .models import PersonalCard
        current=PersonalCard.objects.get(pk=card.pk)
        self.assertEqual(current.state,'active')
        self.assertEqual(current.body,'보존 메모')
        self.assertTrue(current.checklist[0]['done'])
        self.assertEqual(current.priority,'normal')
        self.assertEqual(current.column.color,'green')
        self.assertEqual(current.column.workflow_role,'done')

    def test_additive_migration_preserves_existing_owner_and_completed_task(self):
        executor=MigrationExecutor(connection)
        executor.migrate([('tasks','0009_employee_is_active')])
        old=executor.loader.project_state([('tasks','0009_employee_is_active')]).apps
        try:
            U=old.get_model('auth','User')
            owner=U.objects.create(username='migration-owner',is_superuser=True,is_staff=True,is_active=True,password='unchanged-password-hash')
            dep=old.get_model('tasks','Department').objects.create(name='기존 팀')
            employee=old.get_model('tasks','Employee').objects.create(name='기존 직원',department=dep,role='manager')
            task=old.get_model('tasks','Task').objects.create(title='보존할 완료 업무',assignee=employee,
                start_date=date(2026,1,1),due_date=date(2026,1,2),status='done',progress=100)
            old.get_model('tasks','TaskEvent').objects.create(task=task,author=owner,author_name='기존 관리자',kind='approved',body='보존할 승인 기록')
        finally:
            executor=MigrationExecutor(connection)
            executor.migrate([('tasks','0013_personalcard_priority_personalcolumn_color_and_more')])
        from django.contrib.auth import get_user_model
        from .models import Task,Employee
        user=get_user_model().objects.get(pk=owner.pk)
        self.assertTrue(user.is_superuser and user.is_staff and user.is_active)
        self.assertEqual(user.password,'unchanged-password-hash')
        migrated=Task.objects.get(pk=task.pk)
        self.assertEqual(migrated.status,'done')
        self.assertEqual(migrated.workflow_version,1)
        self.assertEqual(migrated.events.get().body,'보존할 승인 기록')
        self.assertEqual(Employee.objects.get(pk=employee.pk).position,'leader')
