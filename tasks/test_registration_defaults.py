from datetime import datetime,time,timedelta,timezone as dt_timezone
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.test import TestCase,override_settings
from django.utils import timezone
from .models import Department,Employee,Task
from .collaboration import OfficialTaskForm


@override_settings(WORKHUB_REQUIRE_HIWORKS=False,WORKHUB_SCHEDULER_ENABLED=False,
                   PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class RegistrationDefaultsTests(TestCase):
    def setUp(self):
        self.root=get_user_model().objects.create_superuser('registration-operator')
        self.emp=Employee.objects.create(name='기본값 검증',department=Department.objects.create(name='기본값팀'))
        self.client.force_login(self.root)
        self.now=datetime(2026,11,9,15,45,36,tzinfo=dt_timezone.utc)

    def test_new_form_defaults_use_seoul_date_and_minute(self):
        with patch('tasks.forms.timezone.now',return_value=self.now):
            form=OfficialTaskForm(user=self.root)
        self.assertEqual(str(form.initial['start_date']),'2026-11-10')
        self.assertEqual(form.initial['start_time'],time(0,45))

    def test_current_browser_form_has_date_time_and_required_badges(self):
        response=self.client.get('/work/new/')
        self.assertEqual(response.status_code,200)
        self.assertContains(response,'name="start_time"')
        self.assertContains(response,'type="time"')
        self.assertContains(response,'required-badge')
        form=response.context['form']
        self.assertTrue(form.fields['title'].required)
        self.assertTrue(form.fields['assignee'].required)
        self.assertTrue(form.fields['start_date'].required)
        self.assertTrue(form.fields['due_date'].required)
        self.assertFalse(form.fields['start_time'].required)
        self.assertFalse(form.fields['description'].required)

    def data(self,**kwargs):
        day=timezone.localdate()
        return {'title':'시각 저장','assignee':self.emp.pk,'start_date':day,'start_time':'09:20',
            'due_date':day,'status':'todo','importance':'normal','priority':3,
            'allocation':'shared','revision':0,'management_mode':'event',**kwargs}

    def test_user_chosen_time_persists_and_detail_displays_it(self):
        response=self.client.post('/work/new/',self.data())
        self.assertEqual(response.status_code,302)
        obj=Task.objects.get();self.assertEqual(obj.start_time,time(9,20))
        page=self.client.get(response['Location'])
        self.assertContains(page,'09:20')
        self.assertContains(page,'<th>진행 상태</th>',html=True)
        self.assertNotContains(page,'<th>수행 완료</th>',html=True)
        series=self.client.get(f'/work/series/{obj.pk}/')
        self.assertEqual(series.status_code,200)
        self.assertEqual(series.context['form'].initial['starts_at'].time(),time(9,20))

    def test_validation_failure_keeps_user_input_not_current_time(self):
        data=self.data(title='',start_time='08:15')
        response=self.client.post('/work/new/',data)
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.context['form']['start_time'].value(),'08:15')
        self.assertContains(response,'value="08:15"')

    def test_edit_never_resets_saved_time_to_now(self):
        self.client.post('/work/new/',self.data())
        obj=Task.objects.get()
        form=OfficialTaskForm(instance=obj,user=self.root)
        self.assertEqual(form.initial['start_time'],time(9,20))
        obj.start_time=None;obj.save()
        self.assertIsNone(OfficialTaskForm(instance=obj,user=self.root).initial['start_time'])

    def test_clear_optional_time_does_not_force_activity_end(self):
        response=self.client.post('/work/new/',self.data(start_time=''))
        self.assertEqual(response.status_code,302)
        obj=Task.objects.get();self.assertIsNone(obj.start_time);self.assertIsNone(obj.activity_end)

    def test_explicit_date_initial_is_preserved(self):
        day=timezone.localdate()+timedelta(days=3)
        form=OfficialTaskForm(user=self.root,initial={'start_date':day,'start_time':time(10,30)})
        self.assertEqual(form.initial['start_date'],day)
        self.assertEqual(form.initial['start_time'],time(10,30))
