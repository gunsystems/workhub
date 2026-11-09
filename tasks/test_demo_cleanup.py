from io import StringIO
from tempfile import TemporaryDirectory
from pathlib import Path
from unittest.mock import patch
from django.test import TestCase
from django.core.management import call_command,CommandError
from django.contrib.auth import get_user_model
from tasks.models import Employee,Task,TaskEvent,Department


class DemoCleanupTests(TestCase):
    def setUp(self):
        call_command('seed_demo',stdout=StringIO())

    def test_dry_run_does_not_delete_and_apply_preserves_real_staff(self):
        user=get_user_model().objects.create_user('real-user')
        real=Employee.objects.create(name='실제 직원',department=Department.objects.first(),user=user)
        call_command('cleanup_seed_demo',stdout=StringIO())
        self.assertEqual(Task.objects.count(),9)
        with TemporaryDirectory(dir=Path.cwd()) as temp,patch('tasks.management.commands.cleanup_seed_demo.Path') as path:
            path.return_value.resolve.return_value.parent=Path(temp)
            call_command('cleanup_seed_demo',apply=True,stdout=StringIO())
            self.assertEqual(len(list(Path(temp).glob('workhub-demo-backup-*.json'))),1)
        self.assertEqual(Employee.objects.count(),1)
        self.assertEqual(Employee.objects.get().pk,real.pk)
        self.assertTrue(get_user_model().objects.filter(pk=user.pk).exists())
        self.assertFalse(Task.objects.exists())

    def test_changed_demo_task_blocks_every_deletion(self):
        task=Task.objects.first();task.description='실제 작업으로 변경';task.save()
        with self.assertRaises(CommandError): call_command('cleanup_seed_demo',apply=True,stdout=StringIO())
        self.assertEqual(Task.objects.count(),9)
        self.assertEqual(Employee.objects.count(),4)

    def test_feedback_requires_explicit_review_option(self):
        TaskEvent.objects.create(task=Task.objects.first(),kind='feedback',body='예시 테스트',author_name='테스트')
        with self.assertRaises(CommandError): call_command('cleanup_seed_demo',stdout=StringIO())
        output=StringIO()
        call_command('cleanup_seed_demo',include_feedback=True,stdout=output)
        self.assertIn('"feedback": 1',output.getvalue())
        self.assertEqual(TaskEvent.objects.count(),1)
