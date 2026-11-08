from datetime import timedelta
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone
from .models import Department, Employee, Task, TaskParticipation, WorkNotification, HiworksLink, HiworksDelivery, HiworksReview
from .permissions import visible_tasks, can_edit, can_view_company
from .hiworks import directory_rows, enqueue, deliver_pending

@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class PriorityTests(TestCase):
    def setUp(self):
        U=get_user_model()
        self.admin=U.objects.create_superuser('root',password='password')
        self.user=U.objects.create_user('worker',email='worker@gunsystems.co.kr')
        self.other=U.objects.create_user('other')
        self.dept=Department.objects.create(name='테스트 수행팀')
        self.foreign=Department.objects.create(name='테스트 타팀')
        self.employee=Employee.objects.create(name='직원',user=self.user,department=self.dept)
        self.peer=Employee.objects.create(name='동료',user=self.other,department=self.dept)
        self.outsider=Employee.objects.create(name='타팀',department=self.foreign)
        today=timezone.localdate()
        self.task=Task.objects.create(title='상위자 공동 배정',creator=self.admin,assignee=self.outsider,workflow_version=2,start_date=today,due_date=today)
        TaskParticipation.objects.create(task=self.task,employee=self.peer)

    def test_team_shared_visibility_dedup_and_draft_privacy(self):
        TaskParticipation.objects.create(task=self.task,employee=self.employee)
        self.assertEqual(list(visible_tasks(self.user)),[self.task])
        self.assertFalse(can_edit(self.user,self.task))
        self.task.status='draft';self.task.save()
        self.assertFalse(visible_tasks(self.user).exists())
        self.assertTrue(visible_tasks(self.admin).filter(pk=self.task.pk).exists())

    def test_head_can_monitor_not_administer_or_edit(self):
        self.employee.position='head';self.employee.save()
        self.task.assignments.all().delete()
        self.assertTrue(can_view_company(self.user))
        self.assertTrue(visible_tasks(self.user).filter(pk=self.task.pk).exists())
        self.assertFalse(can_edit(self.user,self.task))
        self.client.force_login(self.user)
        self.assertEqual(self.client.get('/work/hiworks/').status_code,403)
        self.task.status='draft';self.task.save()
        self.assertFalse(visible_tasks(self.user).exists())

    def test_calendar_limits_month_and_filters_collaborator_team(self):
        for i in range(10):
            Task.objects.create(title=f'밀집 {i}',assignee=self.peer,start_date=self.task.start_date,due_date=self.task.due_date)
        self.client.force_login(self.user)
        response=self.client.get('/',{'department':self.dept.pk})
        day=next(d for d in response.context['days'] if d['date']==self.task.start_date)
        self.assertEqual(day['total'],11);self.assertEqual(day['more'],7);self.assertEqual(len(day['tasks']),4)
        response=self.client.get('/',{'view':'day','department':self.dept.pk})
        self.assertEqual(len(response.context['days'][0]['tasks']),11)

    @override_settings(HIWORKS_DIRECTORY_TOKEN='directory-test')
    @patch('tasks.hiworks.api')
    def test_directory_accepts_company_email_only_and_no_implicit_changes(self, api):
        api.return_value={'entries':[{'user_no':1,'mail_id':'worker','name':'직원','email':'worker@gunsystems.co.kr'},{'user_no':2,'mail_id':'evil','name':'외부','email':'evil@example.com'}]}
        self.assertEqual(len(directory_rows()),1)
        self.assertEqual(HiworksLink.objects.count(),0)

    @patch('tasks.hiworks_views.directory_rows')
    def test_review_apply_and_replay_protection(self, rows):
        rows.return_value=[{'remote_id':'42','user_id':'worker','email':'worker@gunsystems.co.kr','name':'직원'}]
        self.client.force_login(self.admin)
        response=self.client.post('/work/hiworks/',{'action':'preview'})
        self.assertEqual(response.status_code,200)
        review=HiworksReview.objects.get()
        payload={'action':'apply','review':review.pk,'select_0':'on','employee_0':self.employee.pk}
        self.assertEqual(self.client.post('/work/hiworks/',payload).status_code,302)
        self.assertFalse(HiworksLink.objects.get().notifications_enabled)
        self.assertEqual(self.client.post('/work/hiworks/',payload).status_code,404)
        self.employee.refresh_from_db();self.assertEqual(self.employee.role,'employee')

    @override_settings(HIWORKS_NOTIFY_TOKEN='notify-test')
    @patch('tasks.hiworks.api')
    def test_outbox_once_and_permission_recheck(self, api):
        HiworksLink.objects.create(employee=self.employee,user_id='worker',remote_id='42',email=self.user.email,notifications_enabled=True)
        notification=WorkNotification.objects.create(user=self.user,task=self.task,message='社内')
        enqueue(notification);enqueue(notification)
        self.assertEqual(HiworksDelivery.objects.count(),1)
        deliver_pending();deliver_pending()
        self.assertEqual(api.call_count,1)
        self.assertEqual(HiworksDelivery.objects.get().state,'sent')
        notification=WorkNotification.objects.create(user=self.user,task=self.task,message='private')
        enqueue(notification)
        self.peer.department=self.foreign;self.peer.save()
        deliver_pending();self.assertEqual(api.call_count,1)
        self.assertEqual(HiworksDelivery.objects.latest('pk').state,'skipped')

    @override_settings(HIWORKS_NOTIFY_TOKEN='notify-test')
    @patch('tasks.hiworks.api')
    def test_uncertain_delivery_not_retried(self, api):
        from django.core.exceptions import ValidationError
        api.side_effect=ValidationError('응답 확인 실패')
        HiworksLink.objects.create(employee=self.employee,user_id='worker',remote_id='42',email=self.user.email,notifications_enabled=True)
        enqueue(WorkNotification.objects.create(user=self.user,task=self.task,message='test'))
        deliver_pending();deliver_pending()
        self.assertEqual(api.call_count,1)
        self.assertEqual(HiworksDelivery.objects.get().state,'failed')


class TransportTests(TestCase):
    @patch('tasks.hiworks.build_opener')
    def test_official_raw_organization_response(self, opener):
        import json
        from .hiworks import api
        payload={'node_id':'root','name':'회사','entries':[],'nodes':[]}
        opener.return_value.open.return_value.__enter__.return_value.read.return_value=json.dumps(payload).encode()
        self.assertEqual(api('/hrm/v2/organizations','test'),payload)
        request=opener.return_value.open.call_args.args[0]
        self.assertEqual(request.get_method(),'GET')

    @patch('tasks.hiworks.build_opener')
    def test_notify_uses_json_array(self, opener):
        import json
        from .hiworks import api
        opener.return_value.open.return_value.__enter__.return_value.read.return_value=b'{"code":"SUC","message":""}'
        api('/office/v2/notify','test',{'user_list':['worker'],'message':'notice','link':'https://workhub.gunsystems.co.kr/'})
        request=opener.return_value.open.call_args.args[0]
        self.assertEqual(json.loads(request.data)['user_list'],['worker'])
        self.assertEqual(request.get_header('Content-type'),'application/json')
