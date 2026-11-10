from datetime import timedelta
from importlib import import_module
from unittest.mock import patch
from django.apps import apps
from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase,override_settings
from django.utils import timezone
from .models import Department,Employee,Task,ScheduleRegistration
from .schedule_control import observe_schedule
from .documents import DocumentForm
from .collaboration import OfficialTaskForm
from .requests import RequestForm
from .reports import ReportForm


@override_settings(WORKHUB_REQUIRE_HIWORKS=False,WORKHUB_SCHEDULER_ENABLED=False,PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class UploadRegistrationTests(TestCase):
    def setUp(self):
        self.user=get_user_model().objects.create_superuser('uploadadmin')
        team=Department.objects.create(name='첨부 검증팀');self.emp=Employee.objects.create(user=self.user,name='검증',department=team)
        self.now=timezone.now();self.local=timezone.localtime(self.now)
        self.task=Task.objects.create(title='지금 등록한 업무',assignee=self.emp,start_date=self.local.date(),
            start_time=(self.local-timedelta(minutes=25)).time().replace(tzinfo=None),due_date=self.local.date()+timedelta(days=3))
    def test_writing_today_for_more_than_five_minutes_is_not_late(self):
        with patch('tasks.schedule_control.timezone.now',return_value=self.now):observe_schedule(self.task,'created',{})
        self.assertFalse(ScheduleRegistration.objects.get(task=self.task).late)
    def test_explicit_past_activity_and_past_date_still_late(self):
        for explicit in [True,False]:
            ScheduleRegistration.objects.filter(task=self.task).delete()
            self.task.activity_start=self.now-timedelta(hours=1) if explicit else None
            self.task.start_date=self.local.date() if explicit else self.local.date()-timedelta(days=1)
            with patch('tasks.schedule_control.timezone.now',return_value=self.now):observe_schedule(self.task,'created',{})
            self.assertTrue(ScheduleRegistration.objects.get(task=self.task).late)
    def test_migration_corrects_only_default_time_same_day_and_preserves_evidence(self):
        plan={'start_date':str(self.local.date()),'start_time':'09:00:00','activity_start':'None','due_date':str(self.task.due_date)}
        row=ScheduleRegistration.objects.create(task=self.task,registered_at=self.now,late=True,initial_plan=plan)
        migration=import_module('tasks.migrations.0031_correct_default_start_registration')
        class Editor:pass
        editor=Editor();editor.connection=connection
        migration.correct_registration_flags(apps,editor);row.refresh_from_db()
        self.assertFalse(row.late);self.assertEqual(row.initial_plan,plan);self.assertEqual(row.registered_at,self.now)
        row.late=True;row.initial_plan={**plan,'activity_start':str(self.now-timedelta(hours=1))};row.save()
        migration.correct_registration_flags(apps,editor);row.refresh_from_db();self.assertTrue(row.late)
    def test_all_four_forms_declare_matching_drag_drop_limits(self):
        forms=[(DocumentForm(),'file',20),(OfficialTaskForm(user=self.user),'attachment',20),
               (RequestForm(user=self.user),'attachment',20),(ReportForm(user=self.user),'attachment',10)]
        for form,field,mb in forms:
            widget=form.fields[field].widget
            self.assertEqual(widget.attrs['data-upload-limit'],str(mb*1024*1024));self.assertIn('.pdf',widget.attrs['accept'])
    def test_upload_success_message_is_visible_on_landing_page(self):
        from django.contrib.messages import get_messages
        from django.core.files.uploadedfile import SimpleUploadedFile
        self.client.force_login(self.user)
        with override_settings(GOOGLE_DRIVE_CREDENTIALS_FILE='example',GOOGLE_DRIVE_SHARED_DRIVE_ID='drive'),patch('tasks.drive_storage.DriveClient.__init__',return_value=None),patch('tasks.drive_storage.DriveClient.upload',return_value='drive/probe'):
            r=self.client.post(f'/work/documents/task/{self.task.pk}/add/',{'kind':'reference','file':SimpleUploadedFile('guide.txt',b'example')},follow=True)
        self.assertContains(r,'파일 업로드가 완료되었습니다.');self.assertContains(r,'role="status"')
