import uuid
from datetime import timedelta
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone
from .models import Department, Employee, Meeting, MeetingAttendee, MeetingMail, MicrosoftConnection, Task, WorkNotification
from .meetings import MeetingForm, save, deliver_pending
from . import microsoft_meetings as ms


@override_settings(WORKHUB_REQUIRE_HIWORKS=False,WORKHUB_SCHEDULER_ENABLED=False,
    PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'],WORKHUB_BASE_URL='https://workhub.example.test')
class MeetingTests(TestCase):
    def setUp(self):
        U=get_user_model();self.a=U.objects.create_user('meeting_a',email='a@example.test');self.b=U.objects.create_user('meeting_b',email='b@example.test');self.c=U.objects.create_user('meeting_c',email='c@example.test')
        one=Department.objects.create(name='미팅 검증팀 A');two=Department.objects.create(name='미팅 검증팀 B')
        self.e=Employee.objects.create(name='가상 주최자',user=self.a,department=one)
        self.f=Employee.objects.create(name='가상 참석자',user=self.b,department=two)
        self.g=Employee.objects.create(name='가상 비참석자',user=self.c,department=two)
        self.client.force_login(self.a)
    def payload(self,**changes):
        data={'title':'가상 미팅','starts_at':'2030-01-07T10:00','duration':60,'mode':'office','attendees':[self.f.pk],
            'provider':'link','token':str(uuid.uuid4()),'revision':0,'description':'자료 검토','location':'회의실 A'}
        data.update(changes);return data
    def create(self,**changes):
        form=MeetingForm(self.payload(**changes),user=self.a)
        self.assertTrue(form.is_valid(),form.errors);return save(form,self.a)
    def connection(self):
        return MicrosoftConnection.objects.create(user=self.a,subject='oid-a',email=self.a.email,encrypted_refresh_token=ms.cipher().encrypt(b'refresh-test-only').decode())
    def test_meeting_in_calendar_reports_and_duplicate_submission(self):
        payload=self.payload();form=MeetingForm(payload,user=self.a);self.assertTrue(form.is_valid());item=save(form,self.a)
        same=save(form,self.a);self.assertEqual(item.pk,same.pk);self.assertEqual(Meeting.objects.count(),1)
        self.assertEqual(item.task.management_mode,'event');self.assertEqual(item.task.participants.count(),2)
        self.assertEqual(item.task.activity_end-item.task.activity_start,timedelta(hours=1))
        self.assertContains(self.client.get('/?date=2030-01-07'),'가상 미팅')
        self.client.force_login(self.b);self.assertContains(self.client.get('/?date=2030-01-07'),'가상 미팅')
    def test_attendee_can_respond_but_not_edit_or_finish(self):
        item=self.create();self.client.force_login(self.b)
        response=self.client.post(f'/work/meetings/{item.pk}/',{'action':'respond','response':'yes','revision':0})
        self.assertEqual(response.status_code,302);self.assertEqual(item.attendees.get(employee=self.f).response,'yes')
        self.assertEqual(self.client.get(f'/work/meetings/{item.pk}/edit/').status_code,403)
        self.assertEqual(self.client.post(f'/work/meetings/{item.pk}/',{'action':'finish','revision':0}).status_code,403)
        self.assertTrue(WorkNotification.objects.filter(user=self.a,task=item.task).exists())
    def test_workflow_buttons_cannot_mutate_meeting(self):
        item=self.create()
        from .collaboration import change
        from django.core.exceptions import ValidationError
        with self.assertRaises(ValidationError):change(self.a,item.task_id,'submit',{'revision':item.task.revision,'body':'금지'})
        self.assertEqual(self.client.get(f'/tasks/{item.task_id}/')['Location'],f'/work/meetings/{item.pk}/')
        self.assertEqual(self.client.get(f'/work/series/{item.task_id}/').status_code,403)
    def test_changed_time_resets_response_and_stale_update_is_blocked(self):
        item=self.create();row=item.attendees.get(employee=self.f);row.response='yes';row.save()
        data=self.payload(token=str(item.transaction_id),starts_at='2030-01-07T11:00')
        form=MeetingForm(data,user=self.a,item=item);self.assertTrue(form.is_valid(),form.errors);save(form,self.a,item)
        self.assertEqual(item.attendees.get(employee=self.f).response,'pending')
        from django.core.exceptions import ValidationError
        with self.assertRaises(ValidationError):save(form,self.a,item)
    def test_removed_attendee_loses_cross_team_document_access(self):
        item=self.create();data=self.payload(attendees=[],token=str(item.transaction_id))
        form=MeetingForm(data,user=self.a,item=item);self.assertTrue(form.is_valid(),form.errors);save(form,self.a,item)
        self.client.force_login(self.b);self.assertEqual(self.client.get(f'/work/meetings/{item.pk}/').status_code,404)
        self.assertTrue(MeetingMail.objects.filter(meeting=item,user=self.b,removed=True).exists())
    def test_nonparticipant_cannot_respond(self):
        item=self.create();self.client.force_login(self.c)
        # Same-team oversight may allow viewing, but never responding for someone else.
        self.assertEqual(self.client.post(f'/work/meetings/{item.pk}/',{'action':'respond','response':'yes','revision':0}).status_code,403)
    def test_validation_online_location_duration_and_guest_rules(self):
        for changes,field in [({'mode':'online','join_url':''},'join_url'),({'mode':'outside','location':''},'location'),
            ({'duration':0},'duration'),({'join_url':'http://example.test/meet','mode':'online'},'join_url'),
            ({'guests':'guest@example.test'},'guests'),({'mode':'online','provider':'teams'},'provider')]:
            form=MeetingForm(self.payload(**changes),user=self.a);self.assertFalse(form.is_valid());self.assertIn(field,form.errors)
    def test_form_and_detail_have_materials_and_no_duplicate_dates(self):
        response=self.client.get('/work/meetings/new/');self.assertContains(response,'미팅 일시');self.assertNotContains(response,'name="due_date"')
        item=self.create();response=self.client.get(f'/work/meetings/{item.pk}/');self.assertContains(response,'자료 추가');self.assertContains(response,'미팅 종료')
        self.assertContains(response,'미팅 수정');self.assertNotContains(response,'배정 확인')
    def test_finish_and_cancel_are_explicit_and_notify(self):
        item=self.create();self.client.post(f'/work/meetings/{item.pk}/',{'action':'finish','revision':0,'result':'합의 내용'})
        item.refresh_from_db();self.assertEqual(item.task.status,'done');self.assertEqual(item.result,'합의 내용')
        other=self.create(title='취소할 가상 미팅');self.client.post(f'/work/meetings/{other.pk}/',{'action':'cancel','revision':0})
        other.refresh_from_db();self.assertEqual(other.task.status,'cancelled');self.assertContains(self.client.get(f'/work/meetings/{other.pk}/'),'[취소]')
    @patch('accounts.services.deliver')
    def test_mail_is_once_and_old_revisions_are_not_sent(self,deliver):
        item=self.create();deliver_pending();deliver_pending();self.assertEqual(deliver.call_count,1)
        self.assertIn('미팅 확인하기',deliver.call_args.kwargs['html_body'])
        item.revision+=1;item.save();MeetingMail.objects.filter(meeting=item).update(state='pending');deliver_pending()
        self.assertEqual(deliver.call_count,1)
    def test_teams_requires_owner_connection_and_no_external_account_creation(self):
        self.connection();item=self.create(mode='online',provider='teams',guests='guest@example.test')
        self.assertEqual(item.sync_state,'pending');self.assertEqual(item.guests,['guest@example.test']);self.assertEqual(get_user_model().objects.count(),3)
    @patch('tasks.microsoft_meetings.request_json')
    @patch('tasks.microsoft_meetings.access_token',return_value='test-token')
    def test_teams_creation_update_and_cancel_keep_event_identity(self,token,graph):
        self.connection();item=self.create(mode='online',provider='teams')
        graph.return_value={'id':'event-one','onlineMeeting':{'joinUrl':'https://teams.microsoft.com/l/meetup-join/test'}}
        ms.sync_one(item.pk);item.refresh_from_db();self.assertEqual(item.sync_state,'synced');self.assertEqual(item.event_id,'event-one')
        args=graph.call_args.args;self.assertEqual(args[1],'POST');self.assertEqual(args[2]['transactionId'],str(item.transaction_id))
        self.assertEqual(args[2]['start']['timeZone'],'UTC');self.assertEqual(args[2]['start']['dateTime'],'2030-01-07T01:00:00')
        item.sync_state='pending';item.save();ms.sync_one(item.pk);self.assertEqual(graph.call_args.args[1],'PATCH');self.assertNotIn('body',graph.call_args.args[2])
        item.task.status='cancelled';item.task.save();item.sync_state='pending';item.save();graph.return_value={};ms.sync_one(item.pk)
        self.assertEqual(graph.call_args.args[1],'DELETE');item.refresh_from_db();self.assertEqual(item.sync_state,'synced')
    @patch('tasks.microsoft_meetings.request_json',side_effect=ms.GraphError())
    @patch('tasks.microsoft_meetings.access_token',return_value='test-token')
    def test_ambiguous_write_is_not_automatically_retried(self,token,graph):
        self.connection();item=self.create(mode='online',provider='teams');ms.sync_one(item.pk);ms.synchronize()
        item.refresh_from_db();self.assertEqual(item.sync_state,'unknown');self.assertEqual(graph.call_count,1)
    @patch('tasks.microsoft_meetings.request_json')
    @patch('tasks.microsoft_meetings.access_token',return_value='test-token')
    def test_created_event_id_retained_when_join_link_not_ready(self,token,graph):
        self.connection();item=self.create(mode='online',provider='teams');graph.side_effect=[{'id':'created-one'},{}]
        ms.sync_one(item.pk);item.refresh_from_db();self.assertEqual(item.event_id,'created-one');self.assertEqual(item.sync_state,'unknown')
    def test_sync_in_progress_blocks_edits_and_cancellation(self):
        item=self.create();item.sync_state='sending';item.save()
        response=self.client.post(f'/work/meetings/{item.pk}/',{'action':'cancel','revision':0});self.assertEqual(response.status_code,302)
        item.refresh_from_db();self.assertNotEqual(item.task.status,'cancelled')
    def test_oauth_state_rejects_cross_user_without_network(self):
        with patch.object(ms,'token_request') as call:
            self.client.get('/work/meetings/microsoft/callback/?state=bad&code=private');call.assert_not_called()
        self.assertFalse(MicrosoftConnection.objects.exists())
    def test_meeting_uses_attendance_alert_not_work_assignment_or_score(self):
        item=self.create()
        from .alerts import action_alerts
        from .schedule_control import deadline_state
        from .metrics import employee_metrics
        self.assertFalse(item.task.awaiting_acceptance);self.assertIsNone(deadline_state(item.task))
        self.assertTrue(any(row['title']=='미팅 참석 응답 필요' for row in action_alerts(self.b)))
        self.assertFalse(any(row['title']=='배정 확인 필요' for row in action_alerts(self.b)))
        self.assertEqual(employee_metrics(self.e,[item.task],item.task.start_date,item.task.due_date)['eligible'],0)
    def test_upcoming_reminder_once_excludes_declined_and_cancelled(self):
        from .meetings import remind_upcoming
        item=self.create();now=item.task.activity_start-timedelta(minutes=10)
        item.attendees.filter(employee=self.f).update(response='no')
        WorkNotification.objects.all().delete();remind_upcoming(now);remind_upcoming(now)
        self.assertEqual(WorkNotification.objects.filter(task=item.task).count(),1)
        self.assertEqual(WorkNotification.objects.get().user,self.a)
        item.task.status='cancelled';item.task.save();item.attendees.update(reminded_revision=-1);remind_upcoming(now)
        self.assertEqual(WorkNotification.objects.count(),1)
    def test_ambiguous_initial_creation_must_be_recovered_before_cancel(self):
        self.connection();item=self.create(mode='online',provider='teams');item.sync_state='unknown';item.save()
        self.client.post(f'/work/meetings/{item.pk}/',{'action':'cancel','revision':0})
        item.refresh_from_db();self.assertNotEqual(item.task.status,'cancelled')
        self.client.post(f'/work/meetings/{item.pk}/',{'action':'retry','revision':0});item.refresh_from_db();self.assertEqual(item.sync_state,'pending')
    def test_cancelled_and_unsynchronized_teams_cannot_be_trashed(self):
        self.connection();item=self.create(mode='online',provider='teams')
        admin=get_user_model().objects.create_superuser('meeting_admin');self.client.force_login(admin)
        response=self.client.post(f'/work/tasks/{item.task_id}/delete/',{'revision':item.task.revision,'reason':'가상 자료 정리'})
        self.assertContains(response,'Teams 미팅은 먼저');item.task.refresh_from_db();self.assertFalse(item.task.is_deleted)
    def test_admin_settings_visible_only_to_admin_and_teams_disconnect_guard(self):
        self.connection();item=self.create(mode='online',provider='teams')
        self.assertNotContains(self.client.get('/work/meetings/microsoft/'),'MICROSOFT_CLIENT_SECRET')
        self.client.post('/work/meetings/microsoft/disconnect/');self.assertTrue(MicrosoftConnection.objects.filter(user=self.a).exists())
    def test_calendar_quick_and_work_entry_link_to_meeting(self):
        self.assertContains(self.client.get('/work/register/'),'미팅 등록')
        self.assertContains(self.client.get('/'),'＋ 미팅 등록')
    @override_settings(MICROSOFT_TENANT_ID='11111111-1111-1111-1111-111111111111',MICROSOFT_CLIENT_ID='22222222-2222-2222-2222-222222222222',MICROSOFT_CLIENT_SECRET='test-only')
    @patch('tasks.microsoft_meetings.request_json')
    @patch('tasks.microsoft_meetings.token_request')
    def test_oauth_checks_email_and_teams_provider(self,tokens,graph):
        self.client.post('/work/meetings/microsoft/connect/');state=self.client.session['microsoft_oauth']['state']
        tokens.return_value={'access_token':'access','refresh_token':'refresh','scope':'Calendars.ReadWrite User.Read'}
        graph.side_effect=[{'id':'oid-a','mail':self.a.email}, {'allowedOnlineMeetingProviders':['teamsForBusiness']}]
        response=self.client.get('/work/meetings/microsoft/callback/',{'state':state,'code':'test-code'});self.assertEqual(response.status_code,302)
        connection=MicrosoftConnection.objects.get(user=self.a);self.assertNotEqual(connection.encrypted_refresh_token,'refresh')
        self.client.post('/work/meetings/microsoft/connect/');state=self.client.session['microsoft_oauth']['state'];graph.side_effect=[{'id':'someone','mail':'wrong@example.test'}]
        self.client.get('/work/meetings/microsoft/callback/',{'state':state,'code':'test-code'});connection.refresh_from_db();self.assertEqual(connection.subject,'oid-a')
