from datetime import datetime,date,time,timedelta
from unittest.mock import patch
from urllib.parse import urlparse,parse_qs
from django.contrib.auth import get_user_model
from django.test import TestCase,Client,override_settings
from django.utils import timezone
from .models import (PersonalBoard,PersonalColumn,PersonalCard,PersonalTag,PersonalSchedule,PersonalReminder,
    GoogleCalendarConnection,CalendarExport,CalendarExportPolicy,SchedulerLease,Task,Department,Employee)
from .schedules import SEOUL,next_run,arrange_reminder,arrange_export,completion_followup
from .scheduler import process_rule,tick
from .personal import CardForm,complete_card
from . import google_calendar as google


class ScheduleTests(TestCase):
    def setUp(self):
        self.user=get_user_model().objects.create_user('schedule-owner',email='work@gunsystems.co.kr',password='test')
        self.other=get_user_model().objects.create_user('schedule-other',password='test')
        self.board=PersonalBoard.objects.create(owner=self.user,name='일정')
        self.column=PersonalColumn.objects.create(board=self.board,name='할 일',workflow_role='todo')
        self.card=PersonalCard.objects.create(column=self.column,title='정기 점검',due_date=date(2026,11,9),due_time=time(9),reminder_minutes=10)
        self.client.force_login(self.user)
        self.start=datetime(2026,11,9,9,tzinfo=SEOUL)

    def rule(self,**kwargs):
        rule=PersonalSchedule.objects.create(template_card=self.card,column=self.column,starts_at=self.start,kind='daily',month_day=9,**kwargs)
        rule.next_at=next_run(rule,self.start);rule.save()
        self.card.series=rule;self.card.occurrence_at=self.start;self.card.save()
        return rule

    def test_weekday_weekly_interval_and_clock(self):
        rule=self.rule()
        self.assertEqual(next_run(rule,self.start),datetime(2026,11,10,9,tzinfo=SEOUL))
        rule.kind='weekdays'
        friday=datetime(2026,11,13,9,tzinfo=SEOUL)
        self.assertEqual(next_run(rule,friday),datetime(2026,11,16,9,tzinfo=SEOUL))
        rule.kind='weekly';rule.interval=2;rule.weekdays=[0,2]
        self.assertEqual(next_run(rule,self.start),datetime(2026,11,11,9,tzinfo=SEOUL))
        self.assertEqual(next_run(rule,datetime(2026,11,11,9,tzinfo=SEOUL)),datetime(2026,11,23,9,tzinfo=SEOUL))

    def test_month_day_missing_day_and_nth_weekday(self):
        rule=self.rule();rule.starts_at=datetime(2026,1,31,9,tzinfo=SEOUL);rule.kind='monthly_date';rule.month_day=31
        self.assertEqual(next_run(rule,rule.starts_at),datetime(2026,2,28,9,tzinfo=SEOUL))
        rule.month_end='skip'
        self.assertEqual(next_run(rule,rule.starts_at),datetime(2026,3,31,9,tzinfo=SEOUL))
        rule.starts_at=self.start;rule.kind='monthly_weekday';rule.weekdays=[0];rule.week_number=2
        self.assertEqual(next_run(rule,self.start),datetime(2026,12,14,9,tzinfo=SEOUL))
        rule.week_number=-1
        self.assertEqual(next_run(rule,self.start),datetime(2026,11,30,9,tzinfo=SEOUL))

    def test_generated_at_reminder_time_with_unfinished_previous_and_no_duplicate(self):
        rule=self.rule();self.assertEqual(process_rule(rule.pk,datetime(2026,11,10,8,49,tzinfo=SEOUL)),0)
        self.assertEqual(process_rule(rule.pk,datetime(2026,11,10,8,50,tzinfo=SEOUL)),1)
        self.assertEqual(process_rule(rule.pk,datetime(2026,11,10,9,tzinfo=SEOUL)),0)
        self.assertEqual(PersonalCard.objects.count(),2)
        self.card.refresh_from_db();self.assertEqual(self.card.state,'active')
        occurrence=PersonalCard.objects.exclude(pk=self.card.pk).get()
        self.assertEqual(occurrence.due_time,time(9))
        self.assertEqual(occurrence.reminders.get().notify_at,datetime(2026,11,10,8,50,tzinfo=SEOUL))

    def test_count_and_until_stop(self):
        rule=self.rule(max_count=2)
        self.assertEqual(process_rule(rule.pk,datetime(2026,12,1,tzinfo=SEOUL)),1)
        rule.refresh_from_db();self.assertIsNone(rule.next_at)
        rule.max_count=None;rule.until=date(2026,11,10)
        self.assertIsNone(next_run(rule,datetime(2026,11,10,9,tzinfo=SEOUL)))

    def test_completion_mode_follows_finished_date_and_is_idempotent(self):
        rule=self.rule(mode='completion',interval=3)
        now=datetime(2026,11,12,18,tzinfo=SEOUL)
        completion_followup(self.card,now);self.card.save()
        completion_followup(self.card,now)
        self.assertEqual(self.card.next_occurrence.due_date,date(2026,11,15))
        self.assertEqual(self.card.next_occurrence.due_time,time(9))
        self.assertEqual(PersonalCard.objects.count(),2)

    def test_calendar_completion_does_not_create_next_immediately(self):
        self.rule(mode='calendar')
        complete_card(self.card,self.column);self.card.save()
        self.assertEqual(PersonalCard.objects.count(),1)

    def test_reminders_delivered_only_to_owner_and_can_be_read(self):
        arrange_reminder(self.card)
        tick(now=datetime(2026,11,9,8,50,tzinfo=SEOUL),sync_google=False)
        reminder=self.card.reminders.get();self.assertIsNotNone(reminder.delivered_at)
        self.assertContains(self.client.get('/work/notifications/'),'정기 점검')
        other=Client();other.force_login(self.other)
        self.assertNotContains(other.get('/work/notifications/'),'정기 점검')
        self.assertEqual(other.post(f'/work/personal/reminders/{reminder.pk}/read/').status_code,404)
        self.client.post(f'/work/personal/reminders/{reminder.pk}/read/')
        reminder.refresh_from_db();self.assertIsNotNone(reminder.read_at)

    def test_pause_and_disabled_owner_prevent_generation(self):
        rule=self.rule()
        self.user.is_active=False;self.user.save()
        self.assertEqual(process_rule(rule.pk,datetime(2026,12,1,tzinfo=SEOUL)),0)
        self.user.is_active=True;self.user.save()
        self.client.post(f'/work/personal/schedules/{rule.pk}/toggle/')
        self.assertEqual(process_rule(rule.pk,datetime(2026,12,1,tzinfo=SEOUL)),0)

    def test_scheduler_lease_blocks_overlapping_ticks(self):
        now=timezone.now()
        SchedulerLease.objects.create(key='personal',token='another-worker',expires_at=now+timedelta(minutes=1))
        self.assertEqual(tick(now=now,sync_google=False),{'busy':True})

    def test_builtin_and_private_tags_multiple_selection_and_foreign_rejected(self):
        builtin=PersonalTag.objects.get(name='회의',owner=None)
        private=PersonalTag.objects.create(owner=self.other,name='다른 사람 태그')
        data={'title':'새 카드','column':self.column.pk,'tags':[builtin.pk],'new_tag':'내 점검','due_date':'2026-11-09','due_time':'09:00','recurrence':'daily','schedule_mode':'calendar','repeat_interval':1}
        response=self.client.post('/work/personal/cards/new/',data)
        self.assertEqual(response.status_code,302,response.context['form'].errors if response.status_code==200 else '')
        card=PersonalCard.objects.get(title='새 카드')
        self.assertEqual(card.tags.count(),2)
        self.assertEqual(card.tags.get(name='내 점검').owner,self.user)
        data['tags']=[private.pk]
        self.assertEqual(self.client.post('/work/personal/cards/new/',data).status_code,200)
        self.assertNotContains(self.client.get(f'/work/personal/cards/{self.card.pk}/'),'다른 사람 태그')

    def test_schedule_form_rejects_missing_time_and_mismatched_first_weekday(self):
        data={'title':'카드','column':self.column.pk,'due_date':'2026-11-09','recurrence':'weekly','schedule_mode':'calendar','repeat_weekdays':[2]}
        form=CardForm(data=data,owner=self.user);self.assertFalse(form.is_valid());self.assertIn('due_time',form.errors)
        data['due_time']='09:00'
        form=CardForm(data=data,owner=self.user);self.assertFalse(form.is_valid());self.assertIn('due_date',form.errors)
        data['repeat_weekdays']=[0,2]
        form=CardForm(data=data,owner=self.user);self.assertTrue(form.is_valid(),form.errors)


@override_settings(GOOGLE_CALENDAR_CLIENT_ID='test-client',GOOGLE_CALENDAR_CLIENT_SECRET='test-secret')
class GoogleCalendarTests(TestCase):
    def setUp(self):
        U=get_user_model()
        self.user=U.objects.create_user('calendar-owner',email='company@gunsystems.co.kr',password='test')
        self.other=U.objects.create_user('calendar-other',password='test')
        self.admin=U.objects.create_superuser('calendar-admin',password='test')
        self.board=PersonalBoard.objects.create(owner=self.user,name='개인')
        self.column=PersonalColumn.objects.create(board=self.board,name='할 일')
        self.card=PersonalCard.objects.create(column=self.column,title='개인 제목',body='개인 비밀 메모',due_date=date(2026,11,9),due_time=time(9),calendar_display='minimal')
        self.client.force_login(self.user)

    def connection(self):
        return GoogleCalendarConnection.objects.create(user=self.user,subject='google-subject',email='personal@gmail.com',
            encrypted_refresh_token=google.cipher().encrypt(b'test-refresh-token').decode(),calendar_id='workhub-calendar')

    def test_oauth_offline_state_pkce_and_callback_cannot_change_company_login(self):
        response=self.client.post('/work/calendar/google/connect/')
        query=parse_qs(urlparse(response.url).query)
        self.assertEqual(query['access_type'],['offline'])
        self.assertEqual(query['code_challenge_method'],['S256'])
        self.assertIn(google.SCOPE,query['scope'][0])
        state=self.client.session['google_oauth']['state']
        with patch('tasks.google_calendar.request_json',side_effect=[
            {'refresh_token':'test-refresh-token','access_token':'access','scope':google.SCOPES},
            {'sub':'subject','email':'personal@gmail.com','email_verified':True},{'id':'new-calendar'}]):
            response=self.client.get('/work/calendar/google/callback/',{'state':state,'code':'one-use-code'})
        self.assertEqual(response.status_code,302)
        connection=GoogleCalendarConnection.objects.get(user=self.user)
        self.assertNotIn('test-refresh-token',connection.encrypted_refresh_token)
        self.assertEqual(google.cipher().decrypt(connection.encrypted_refresh_token.encode()),b'test-refresh-token')
        self.user.refresh_from_db();self.assertEqual(self.user.email,'company@gunsystems.co.kr')
        self.assertEqual(connection.email,'personal@gmail.com')
        self.assertEqual(response['Referrer-Policy'],'no-referrer')

    def test_invalid_expired_replayed_or_other_user_state_never_calls_google(self):
        with patch('tasks.google_calendar.request_json') as api:
            self.client.get('/work/calendar/google/callback/',{'state':'wrong','code':'code'})
            self.assertFalse(api.called)
        self.client.post('/work/calendar/google/connect/')
        session=self.client.session;state=session['google_oauth'];state['at']-=601;session['google_oauth']=state;session.save()
        with patch('tasks.google_calendar.request_json') as api:
            self.client.get('/work/calendar/google/callback/',{'state':state['state'],'code':'code'})
            self.assertFalse(api.called)
        self.assertFalse(GoogleCalendarConnection.objects.exists())

    def test_minimal_payload_does_not_include_title_body_or_link(self):
        arrange_export(self.card);export=CalendarExport.objects.get(card=self.card)
        payload=google.payload(export)
        self.assertEqual(payload['summary'],'업무 일정')
        self.assertNotIn('description',payload)
        self.assertIn('+09:00',payload['start']['dateTime'])
        self.card.calendar_display='details';self.card.save()
        export.refresh_from_db()
        payload=google.payload(export)
        self.assertIn('개인 비밀 메모',payload['description'])
        self.assertEqual(payload['visibility'],'private')

    def test_idempotent_sync_and_optout_deletes_only_known_workhub_event(self):
        connection=self.connection();arrange_export(self.card)
        with patch('tasks.google_calendar.request_json',side_effect=[{'access_token':'access'},google.IntegrationError(404),{}]) as api:
            google.sync_connection(connection)
            self.assertEqual(api.call_count,3)
            self.assertTrue(api.call_args_list[-1].args[0].startswith('https://www.googleapis.com/calendar/v3/calendars/workhub-calendar/events'))
        export=CalendarExport.objects.get(card=self.card)
        self.assertTrue(export.event_id)
        with patch('tasks.google_calendar.request_json',return_value={'access_token':'access'}) as api:
            google.sync_connection(connection);self.assertEqual(api.call_count,1)
        self.card.calendar_display='none';self.card.save();arrange_export(self.card)
        with patch('tasks.google_calendar.request_json',return_value={'access_token':'access'}) as api:
            google.sync_connection(connection)
            self.assertEqual(api.call_args_list[-1].args[1],'DELETE')
        export.refresh_from_db();self.assertEqual(export.event_id,'')

    def test_refresh_error_requires_reconnect_without_exposing_provider_details(self):
        connection=self.connection()
        with patch('tasks.google_calendar.request_json',side_effect=google.IntegrationError(400)):
            google.sync_connection(connection)
        connection.refresh_from_db();self.assertEqual(connection.status,'reconnect')
        self.assertNotIn('test-refresh-token',connection.error)

    def test_disconnect_revokes_and_stops_background_access(self):
        connection=self.connection()
        with patch('tasks.google_calendar.request_json',return_value={}) as api:
            self.client.post('/work/calendar/google/disconnect/')
            self.assertIn('/revoke',api.call_args.args[0])
        connection.refresh_from_db();self.assertEqual(connection.encrypted_refresh_token,'')
        with patch('tasks.google_calendar.request_json') as api:
            google.sync_connection(connection);self.assertFalse(api.called)

    def test_settings_are_owner_only_and_official_export_requires_policy_and_assignee(self):
        self.connection()
        self.client.force_login(self.other)
        self.assertNotContains(self.client.get('/work/calendar/'),'personal@gmail.com')
        self.assertEqual(self.client.post('/work/calendar/policy/',{'allow_official':'on'}).status_code,403)
        dep=Department.objects.create(name='일정 팀')
        emp=Employee.objects.create(user=self.user,name='직원',department=dep)
        task=Task.objects.create(title='회사 업무',assignee=emp,start_date=date(2026,11,1),due_date=date(2026,11,9))
        self.client.force_login(self.user)
        self.assertEqual(self.client.get(f'/work/calendar/task/{task.pk}/').status_code,403)
        self.client.force_login(self.admin);self.client.post('/work/calendar/policy/',{'allow_official':'on'})
        self.client.force_login(self.user)
        self.assertEqual(self.client.post(f'/work/calendar/task/{task.pk}/',{'detail':'title'}).status_code,302)
        self.assertContains(self.client.get(f'/tasks/{task.pk}/'),'Google 캘린더 등록 설정')
        self.assertTrue(CalendarExport.objects.filter(user=self.user,task=task).exists())

    def test_official_calendar_copy_and_per_task_removal_are_separate_and_owner_only(self):
        dep=Department.objects.create(name='제거 테스트 팀')
        emp=Employee.objects.create(user=self.user,name='담당 직원',department=dep)
        task=Task.objects.create(title='검토할 업무',assignee=emp,start_date=date(2026,11,1),due_date=date(2026,11,9))
        CalendarExportPolicy.objects.create(pk=1,allow_official=True)
        path=f'/work/calendar/task/{task.pk}/'
        response=self.client.get(path)
        self.assertContains(response,'1. 등록 날짜')
        self.assertContains(response,'업무 제목과 설명 포함')
        self.assertNotContains(response,'이 업무의 Google 일정 제거')
        self.assertEqual(self.client.post(path,{'detail':'none'}).status_code,200)
        self.assertFalse(CalendarExport.objects.filter(task=task).exists())
        self.client.post(path,{'detail':'title'})
        response=self.client.get(path)
        self.assertContains(response,'이 업무의 Google 일정 제거')
        self.client.force_login(self.other)
        self.assertEqual(self.client.post(path,{'action':'remove'}).status_code,404)
        self.assertEqual(CalendarExport.objects.get(task=task).detail,'title')
        self.client.force_login(self.user)
        csrf_client=Client(enforce_csrf_checks=True);csrf_client.force_login(self.user)
        self.assertEqual(csrf_client.post(path,{'action':'remove'}).status_code,403)
        self.client.post(path,{'action':'remove'})
        self.assertEqual(CalendarExport.objects.get(task=task).detail,'none')
        task.refresh_from_db();self.assertEqual(task.status,'todo')

    def test_calendar_series_uses_rrule_and_does_not_duplicate_children(self):
        rule=PersonalSchedule.objects.create(template_card=self.card,column=self.column,starts_at=datetime(2026,11,9,9,tzinfo=SEOUL),kind='weekly',weekdays=[0,2],interval=2)
        self.card.series=rule;self.card.save();arrange_export(self.card)
        payload=google.payload(CalendarExport.objects.get(card=self.card))
        self.assertIn('BYDAY=MO,WE',payload['recurrence'][0])
        self.assertIn('INTERVAL=2',payload['recurrence'][0])
        child=PersonalCard.objects.create(column=self.column,title='다음 회차',series=rule,occurrence_at=datetime(2026,11,11,9,tzinfo=SEOUL),due_date=date(2026,11,11),due_time=time(9),calendar_display='minimal')
        arrange_export(child)
        self.assertIsNone(google.payload(CalendarExport.objects.get(card=child)))

    def test_oauth_and_policy_changes_require_csrf(self):
        client=Client(enforce_csrf_checks=True);client.force_login(self.user)
        self.assertEqual(client.post('/work/calendar/google/connect/').status_code,403)
        self.assertEqual(client.post('/work/calendar/google/disconnect/').status_code,403)

    @override_settings(GOOGLE_CALENDAR_CLIENT_ID='',GOOGLE_CALENDAR_CLIENT_SECRET='')
    def test_unconfigured_connection_is_honestly_disabled(self):
        response=self.client.get('/work/calendar/')
        self.assertContains(response,'설정을 기다리고')
        self.assertNotContains(response,'Google 계정 선택하고 연결')
