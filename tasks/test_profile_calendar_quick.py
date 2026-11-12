from datetime import date
import json
from django.test import TestCase,override_settings,Client
from django.contrib.auth import get_user_model
from django.utils import timezone
from .models import Employee,Department,StaffEmployment,Task,CalendarQuickSave,SystemAudit
from .calendar_quick import entry_token
from .leave_views import EmploymentForm

@override_settings(WORKHUB_REQUIRE_HIWORKS=False,WORKHUB_SCHEDULER_ENABLED=False,PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class ProfileCalendarTests(TestCase):
    def setUp(self):
        U=get_user_model();self.admin=U.objects.create_superuser('profile_admin');self.a=U.objects.create_user('profile_a',email='a@example.test');self.b=U.objects.create_user('profile_b',email='b@example.test')
        team=Department.objects.create(name='프로필 검증팀')
        self.e=Employee.objects.create(user=self.a,name='가상 직원 A',department=team)
        self.other=Employee.objects.create(user=self.b,name='가상 직원 B',department=team)
        self.info=StaffEmployment.objects.create(employee=self.e,joined_on=date(2020,1,1),birth_date=date(1990,2,3),phone='010-0000-1234',staff_number='T-001',job_title='선임')
        self.day='2027-01-04';self.client.force_login(self.a)
    def payload(self,**changes):
        data={'token':entry_token(self.a),'title':'가상 일정 검증','start_date':self.day,'due_date':self.day,'assignee':self.e.pk,'all_day':'on','action':'save'};data.update(changes);return data
    def test_own_profile_contains_private_info_but_image_payload_does_not(self):
        response=self.client.get('/accounts/me/')
        self.assertContains(response,'T-001');self.assertContains(response,'1990-02-03');self.assertContains(response,'010-0000-1234')
        card=response.context['card'];self.assertEqual(set(card),{'name','staff_number','department','job_title'})
        self.assertNotIn('1990',json.dumps(card));self.assertIn('no-store',response['Cache-Control'])
    def test_other_staff_profile_forbidden_even_by_id_or_query(self):
        self.assertEqual(self.client.get(f'/accounts/employees/{self.other.pk}/profile/').status_code,403)
        response=self.client.get('/accounts/me/?employee_id='+str(self.other.pk));self.assertEqual(response.context['employee'],self.e)
        self.assertEqual(self.client.post('/accounts/me/',{}).status_code,405)
    def test_root_preview_and_unlinked_root_card_absence(self):
        self.client.force_login(self.admin)
        self.assertContains(self.client.get(f'/accounts/employees/{self.e.pk}/profile/'),'T-001')
        self.assertNotContains(self.client.get('/accounts/me/'),'data-card-download')
    def test_private_values_are_masked_in_change_audit(self):
        self.info.phone='010-0000-9876';self.info.birth_date=date(1991,2,3);self.info.save()
        audit=SystemAudit.objects.filter(target_type='StaffEmployment').first()
        text=json.dumps(audit.after,ensure_ascii=False)
        self.assertNotIn('9876',text);self.assertNotIn('1991-02-03',text);self.assertIn('개인정보 수정 항목',text)
    def test_old_employment_form_preserves_new_fields_and_rejects_duplicate_number(self):
        form=EmploymentForm({'employee':self.e.pk,'joined_on':'2020-01-01','ended_on':'','staff_number':'t-001'},instance=self.info)
        self.assertTrue(form.is_valid(),form.errors);item=form.save();self.assertEqual(item.phone,'010-0000-1234');self.assertEqual(item.staff_number,'T-001')
        another=EmploymentForm({'employee':self.other.pk,'joined_on':'2020-01-01','staff_number':'T-001'})
        self.assertFalse(another.is_valid())
    def test_previous_version_insert_can_omit_new_columns(self):
        from django.db import connection
        with connection.cursor() as cursor:
            cursor.execute('INSERT INTO tasks_staffemployment (employee_id,joined_on,ended_on,staff_number,updated_at) VALUES (%s,%s,%s,%s,%s)',[self.other.pk,'2020-01-01',None,'',timezone.now()])
        info=StaffEmployment.objects.get(employee=self.other)
        self.assertEqual(info.phone,'');self.assertEqual(info.job_title,'');self.assertIsNone(info.birth_date)
    def test_calendar_entry_markup_and_form_default_self(self):
        response=self.client.get('/?date='+self.day)
        self.assertContains(response,'data-quick-date="2027-01-04"');self.assertContains(response,'calendar-quick-dialog')
        self.assertEqual(response.context['quick_form'].initial['assignee'],self.e.pk)
    def test_quick_save_uses_workflow_and_is_idempotent(self):
        data=self.payload();response=self.client.post('/work/calendar/quick/',data)
        self.assertEqual(response.status_code,302)
        task=Task.objects.get();self.assertEqual(task.management_mode,'event');self.assertEqual(task.workflow_version,2)
        self.assertEqual(task.start_date,date(2027,1,4));self.assertEqual(task.due_date,task.start_date);self.assertIsNone(task.start_time)
        self.assertEqual(task.participants.get(),self.e);self.assertEqual(task.events.filter(kind='created').count(),1)
        again=self.client.post('/work/calendar/quick/',data);self.assertEqual(again.status_code,302);self.assertEqual(Task.objects.count(),1)
        data['title']='다른 내용';response=self.client.post('/work/calendar/quick/',data);self.assertEqual(response.status_code,200);self.assertEqual(Task.objects.count(),1)
    def test_timed_schedule_stores_korea_time(self):
        response=self.client.post('/work/calendar/quick/',self.payload(all_day='',start_time='10:00',end_time='11:30'))
        self.assertEqual(response.status_code,302);task=Task.objects.get();self.assertEqual(timezone.localtime(task.activity_start).hour,10);self.assertEqual(timezone.localtime(task.activity_end).minute,30)
    def test_invalid_times_or_dates_do_not_save(self):
        for changes in [{'due_date':'2027-01-03'},{'all_day':'','start_time':'12:00','end_time':'11:00'},{'all_day':'','start_time':'','end_time':''}]:
            response=self.client.post('/work/calendar/quick/',self.payload(**changes));self.assertEqual(response.status_code,200);self.assertEqual(Task.objects.count(),0)
        self.assertEqual(CalendarQuickSave.objects.count(),0)
    def test_invalid_or_other_user_token_rejected(self):
        self.assertEqual(self.client.post('/work/calendar/quick/',self.payload(token='bad')).status_code,200)
        self.assertEqual(self.client.post('/work/calendar/quick/',self.payload(token=entry_token(self.b))).status_code,200)
        self.assertEqual(Task.objects.count(),0)
    def test_inactive_assignee_rejected_and_staff_can_assign_active_teammate(self):
        self.other.is_active=False;self.other.save()
        self.client.post('/work/calendar/quick/',self.payload(assignee=self.other.pk));self.assertEqual(Task.objects.count(),0)
        self.other.is_active=True;self.other.save()
        response=self.client.post('/work/calendar/quick/',self.payload(assignee=self.other.pk));self.assertEqual(response.status_code,302);self.assertEqual(Task.objects.get().creation_kind,'request')
    def test_quick_assignee_respects_calendar_scope_and_executive_company_scope(self):
        foreign=Employee.objects.create(name='비공개 타부서',department=Department.objects.create(name='다른 검증팀'))
        self.assertNotContains(self.client.get('/?date='+self.day),'비공개 타부서')
        response=self.client.post('/work/calendar/quick/',self.payload(assignee=foreign.pk))
        self.assertEqual(response.status_code,200);self.assertEqual(Task.objects.count(),0)
        self.e.position='executive';self.e.save()
        response=self.client.post('/work/calendar/quick/',self.payload(assignee=foreign.pk))
        self.assertEqual(response.status_code,302);self.assertEqual(Task.objects.get().assignee,foreign)
    def test_details_preserves_values_without_creating_task(self):
        response=self.client.post('/work/calendar/quick/',self.payload(action='details',all_day='',start_time='14:00',end_time='15:00',description='내용 유지',location='회의실'))
        self.assertEqual(response.status_code,200);form=response.context['form'];self.assertEqual(form.initial['description'],'내용 유지');self.assertEqual(form.initial['location'],'회의실');self.assertEqual(form.initial['activity_start'].hour,14)
        self.assertEqual(form.initial['management_mode'],'event');self.assertEqual(Task.objects.count(),0);self.assertEqual(CalendarQuickSave.objects.count(),0)
        self.assertContains(response,'action="/work/new/"')
    def test_blank_title_can_continue_to_details(self):
        response=self.client.post('/work/calendar/quick/',self.payload(action='details',title=''))
        self.assertEqual(response.status_code,200);self.assertTemplateUsed(response,'tasks/official_form.html')
    def test_csrf_and_anonymous_requests_not_accepted(self):
        client=Client(enforce_csrf_checks=True);client.force_login(self.a)
        self.assertEqual(client.post('/work/calendar/quick/',self.payload()).status_code,403)
        client.logout();self.assertEqual(client.post('/work/calendar/quick/',self.payload()).status_code,403)
        self.assertEqual(Task.objects.count(),0)
