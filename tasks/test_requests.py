from datetime import timedelta
import uuid
from django.contrib.auth import get_user_model
from django.test import TestCase,override_settings
from django.utils import timezone
from .models import Department,Employee,Project,Task,TaskParticipation,WorkRequest,RequestEvent,RequestNotification,RequestAllocation,AssignmentMail
from .permissions import visible_requests,visible_projects

@override_settings(WORKHUB_REQUIRE_HIWORKS=False,WORKHUB_SCHEDULER_ENABLED=False,PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'],EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class RequestFlowTests(TestCase):
    def setUp(self):
        U=get_user_model();self.root=U.objects.create_superuser('root')
        self.sales=Department.objects.get(name='영업팀');self.tech=Department.objects.get(name='보안기술팀');self.other=Department.objects.create(name='검증부서')
        self.users={};self.emp={}
        for name,team,position in [('sales',self.sales,'member'),('saleslead',self.sales,'leader'),('techlead',self.tech,'leader'),('engineer',self.tech,'member'),('helper',self.tech,'member'),('outsider',self.other,'member')]:
            u=U.objects.create_user(name);self.users[name]=u;self.emp[name]=Employee.objects.create(name=name,user=u,department=team,position=position)
        self.today=timezone.localdate();self.end=self.today+timedelta(days=7)
        self.project=Project.objects.create(name='영업 수주 프로젝트',owner=self.emp['saleslead'],department=self.sales,start_date=self.today,due_date=self.end)
        self.client.force_login(self.users['sales'])
    def data(self,**kw):
        return {'title':'엔지니어 투입 요청','description':'솔루션 구축 · 설치 확인서 제출','department':self.tech.pk,'project':self.project.pk,'request_kind':'support','customer':'고객 A','solution':'보안 솔루션','requested_start_date':str(self.today),'requested_due_date':str(self.end),'response_due_date':str(self.today),'headcount':1,**kw}
    def create(self,**kw):
        self.client.force_login(self.users['sales']);r=self.client.post('/intake/new/',self.data(**kw));self.assertEqual(r.status_code,302,getattr(r,'context',None) and r.context['form'].errors);return WorkRequest.objects.latest('pk')
    def assign_data(self,obj,**kw):
        return {'intake':obj.pk,'intake_revision':obj.revision,'title':obj.title,'description':obj.description,'assignee':self.emp['engineer'].pk,'start_date':str(self.today),'due_date':str(self.end),'status':'todo','importance':'normal','priority':3,'management_mode':'report','allocation':'shared','revision':0,'no_approval':'on','assignment_channels_present':'1',**kw}
    def assign(self,obj,**kw):
        self.client.force_login(self.users['techlead']);r=self.client.post(f'/intake/{obj.pk}/assign/',self.assign_data(obj,**kw));self.assertEqual(r.status_code,302,r.context and r.context['form'].errors);obj.refresh_from_db();return obj.task
    def act(self,obj,name,as_user='techlead',**kw):
        self.client.force_login(self.users[as_user]);obj.refresh_from_db();return self.client.post(f'/intake/{obj.pk}/action/{name}/',{'revision':obj.revision,'body':'구체적인 업무 협의 사유',**kw})
    def test_entry_and_direct_request_are_distinct_and_recorded(self):
        r=self.client.get('/work/register/');self.assertContains(r,'내 업무 등록');self.assertContains(r,'업무협조 요청')
        self.assertContains(self.client.get('/work/new/?purpose=request'),'업무 요청 보내기')
        r=self.client.post('/work/new/?purpose=request',{'purpose':'request','title':'직접 업무 요청','assignee':self.emp['engineer'].pk,'start_date':self.today,'due_date':self.end,'importance':'normal','priority':3,'status':'todo','allocation':'shared','revision':0,'no_approval':'on','assignment_channels_present':'1'})
        self.assertEqual(r.status_code,302);task=Task.objects.get(title='직접 업무 요청');self.assertEqual(task.creation_kind,'request')
        self.assertContains(self.client.get('/intake/?box=sent&state=all'),task.title)
    def test_cross_team_project_request_does_not_grant_project_browsing(self):
        obj=self.create();self.assertEqual(obj.source_department,self.sales)
        self.client.force_login(self.users['techlead']);self.assertContains(self.client.get('/intake/?box=received&state=all'),obj.title)
        self.assertContains(self.client.get(f'/intake/{obj.pk}/'),self.project.name)
        self.assertFalse(visible_projects(self.users['techlead']).filter(pk=self.project.pk).exists())
        self.client.force_login(self.users['outsider']);self.assertEqual(self.client.get(f'/intake/{obj.pk}/').status_code,404)
    def test_request_not_visible_to_unassigned_team_members_or_sender_manager(self):
        obj=self.create()
        for name in ['engineer','saleslead']:
            self.client.force_login(self.users[name]);self.assertEqual(self.client.get(f'/intake/{obj.pk}/').status_code,404)
    def test_support_required_fields_dates_and_safe_reference_url(self):
        for field in ['customer','solution','requested_start_date','response_due_date']:
            r=self.client.post('/intake/new/',self.data(**{field:''}));self.assertIn(field,r.context['form'].errors)
        r=self.client.post('/intake/new/',self.data(resource_url='javascript:alert(1)'));self.assertIn('resource_url',r.context['form'].errors)
        r=self.client.post('/intake/new/',self.data(requested_due_date=self.today-timedelta(days=1)));self.assertIn('requested_due_date',r.context['form'].errors)
    def test_double_submit_same_form_does_not_duplicate_request(self):
        key=str(uuid.uuid4());self.client.post('/intake/new/',self.data(submission=key));self.client.post('/intake/new/',self.data(submission=key));self.assertEqual(WorkRequest.objects.count(),1)
    def test_cannot_reference_invisible_project(self):
        foreign=Project.objects.create(name='다른 부서 비공개',owner=self.emp['outsider'],department=self.other,start_date=self.today,due_date=self.end)
        r=self.client.post('/intake/new/',self.data(project=foreign.pk));self.assertIn('project',r.context['form'].errors)
    def test_notification_and_fallback_and_read_scope(self):
        obj=self.create();n=RequestNotification.objects.get(request=obj,user=self.users['techlead']);self.assertFalse(RequestNotification.objects.filter(request=obj,user=self.users['engineer']).exists())
        self.client.force_login(self.users['techlead']);self.assertContains(self.client.get('/work/notifications/'),obj.title);self.assertEqual(self.client.get(f'/intake/notifications/{n.pk}/read/').status_code,405)
        self.client.post(f'/intake/notifications/{n.pk}/read/');n.refresh_from_db();self.assertIsNotNone(n.read_at)
        self.client.force_login(self.users['sales']);self.assertEqual(self.client.post(f'/intake/notifications/{n.pk}/read/').status_code,404)
        self.emp['techlead'].position='member';self.emp['techlead'].save();other=self.create(title='공석 접수');self.assertTrue(RequestNotification.objects.filter(request=other,user=self.root).exists())
    def test_coordinator_can_negotiate_but_cannot_allocate(self):
        obj=self.create(coordinator=self.emp['helper'].pk);self.assertEqual(self.act(obj,'receive','helper').status_code,302)
        self.client.force_login(self.users['helper']);self.assertEqual(self.client.get(f'/intake/{obj.pk}/assign/').status_code,403)
        self.assertEqual(self.act(obj,'reject','helper').status_code,403)
    def test_foreign_coordinator_and_preference_rejected(self):
        r=self.client.post('/intake/new/',self.data(coordinator=self.emp['sales'].pk,preferred_employee=self.emp['sales'].pk));self.assertIn('coordinator',r.context['form'].errors);self.assertIn('preferred_employee',r.context['form'].errors)
    def test_reason_and_stale_revision_do_not_create_extra_events(self):
        obj=self.create();self.assertEqual(self.act(obj,'receive',body=' ').status_code,200);obj.refresh_from_db();self.assertEqual(obj.revision,0)
        self.assertEqual(self.act(obj,'receive').status_code,302)
        r=self.act(obj,'negotiate',revision=0);self.assertContains(r,'최신');self.assertEqual(obj.events.count(),2)
    def test_clarification_edit_retains_history_and_project_boundary(self):
        obj=self.create();self.assertEqual(self.act(obj,'needs_info').status_code,302);obj.refresh_from_db()
        self.client.force_login(self.users['sales']);r=self.client.post(f'/intake/{obj.pk}/edit/',self.data(revision=obj.revision,change_reason='협의에 따라 솔루션 보완',solution='새 솔루션'))
        self.assertEqual(r.status_code,302,r.context and r.context['form'].errors);obj.refresh_from_db();self.assertEqual(obj.solution,'새 솔루션');self.assertEqual(obj.stage,'received');self.assertEqual(obj.events.last().snapshot['before']['지원 솔루션 · 필요 기술'],'보안 솔루션')
    def test_assignment_restricted_to_receiver_and_idempotent(self):
        obj=self.create();self.client.force_login(self.users['sales']);self.assertEqual(self.client.post(f'/intake/{obj.pk}/assign/',self.assign_data(obj)).status_code,403)
        self.client.force_login(self.users['techlead']);bad=self.assign_data(obj,assignee=self.emp['sales'].pk);r=self.client.post(f'/intake/{obj.pk}/assign/',bad);self.assertIn('assignee',r.context['form'].errors)
        task=self.assign(obj);self.assertEqual(task.workflow_version,2);self.assertEqual(task.project,self.project);self.assertEqual(task.creation_kind,'cooperation');self.assertTrue(task.watchers.filter(pk=self.users['sales'].pk).exists());self.assertEqual(obj.staffing.count(),1)
        count=Task.objects.count();self.client.post(f'/intake/{obj.pk}/assign/',self.assign_data(obj));self.assertEqual(Task.objects.count(),count)
        self.client.force_login(self.users['sales']);self.assertContains(self.client.get(f'/tasks/{task.pk}/'),'원본 요청')
    def test_assignment_with_fewer_people_or_changed_dates_requires_reason(self):
        obj=self.create(headcount=2);self.client.force_login(self.users['techlead']);r=self.client.post(f'/intake/{obj.pk}/assign/',self.assign_data(obj));self.assertIn('change_reason',r.context['form'].errors)
        task=self.assign(obj,change_reason='한 명이 순차 수행',start_date=self.today+timedelta(days=1));self.assertEqual(task.start_date,self.today+timedelta(days=1))
    def test_no_individual_clones_or_draft_from_intake(self):
        obj=self.create();self.client.force_login(self.users['techlead']);r=self.client.post(f'/intake/{obj.pk}/assign/',self.assign_data(obj,status='draft'));self.assertIn('status',r.context['form'].errors)
        task=self.assign(obj,allocation='individual',collaborators=[self.emp['helper'].pk]);self.assertEqual(Task.objects.count(),1);self.assertEqual(task.participants.count(),2)
    def test_workload_warning_does_not_block_assignment(self):
        obj=self.create();Task.objects.create(title='기존 겹치는 팀 업무',assignee=self.emp['engineer'],start_date=self.today,due_date=self.end)
        self.client.force_login(self.users['techlead']);r=self.client.get(f'/intake/{obj.pk}/assign/');self.assertContains(r,'기존 겹치는 팀 업무');self.assign(obj)
    def test_per_person_staffing_reason_and_scope(self):
        obj=self.create();task=self.assign(obj,collaborators=[self.emp['helper'].pk]);obj.refresh_from_db();rows=list(task.participants.order_by('pk'))
        data={'revision':obj.revision,'body':'현장 일정에 맞춰 역할 분담','form-TOTAL_FORMS':2,'form-INITIAL_FORMS':2,'form-MIN_NUM_FORMS':0,'form-MAX_NUM_FORMS':100}
        for i,e in enumerate(rows):data.update({f'form-{i}-employee':e.pk,f'form-{i}-role':'설치 지원',f'form-{i}-start_date':self.today,f'form-{i}-end_date':self.end})
        path=f'/intake/{obj.pk}/staffing/';self.assertEqual(self.client.post(path,data).status_code,302);self.assertEqual(obj.staffing.filter(role='설치 지원').count(),2)
        data['revision']=obj.revision;data['form-0-employee']=self.emp['outsider'].pk;self.assertEqual(self.client.post(path,data).status_code,200)
        self.client.force_login(self.users['sales']);self.assertEqual(self.client.get(path).status_code,403)
    def test_cancel_and_reject_are_reasoned_and_block_assignment(self):
        obj=self.create();self.assertEqual(self.act(obj,'reject').status_code,302);self.client.force_login(self.users['techlead']);self.assertEqual(self.client.get(f'/intake/{obj.pk}/assign/').status_code,403)
        other=self.create(title='취소할 요청');self.assertEqual(self.act(other,'cancel','sales').status_code,302);other.refresh_from_db();self.assertEqual(other.state,'취소');self.assertTrue(other.events.exists())
    def test_only_requester_confirms_after_work_done_and_followup_is_new_request(self):
        obj=self.create();task=self.assign(obj);self.assertEqual(self.act(obj,'confirm','sales').status_code,200)
        task.status='done';task.save();self.assertEqual(self.act(obj,'confirm','engineer').status_code,403)
        self.assertEqual(self.act(obj,'followup','sales').status_code,302);self.assertEqual(obj.followups.count(),1);task.refresh_from_db();self.assertEqual(task.status,'done')
        self.assertEqual(self.act(obj,'confirm','sales').status_code,302);obj.refresh_from_db();self.assertEqual(obj.state,'결과 확인 완료')
    def test_readonly_gets_and_assignment_external_channels_off(self):
        obj=self.create();before=(RequestEvent.objects.count(),RequestAllocation.objects.count(),RequestNotification.objects.count())
        self.client.force_login(self.users['techlead'])
        for p in ['/work/register/','/intake/?box=received&state=all',f'/intake/{obj.pk}/',f'/intake/{obj.pk}/assign/']:
            self.assertEqual(self.client.get(p).status_code,200,p)
        self.assertEqual(before,(RequestEvent.objects.count(),RequestAllocation.objects.count(),RequestNotification.objects.count()))
        self.assign(obj);self.assertFalse(AssignmentMail.objects.exists())

    def test_invalid_headcount_is_not_silently_replaced(self):
        r=self.client.post('/intake/new/',self.data(headcount=0));self.assertIn('headcount',r.context['form'].errors)
    def test_reopened_work_requires_requester_confirmation_again(self):
        obj=self.create();task=self.assign(obj);task.status='done';task.save();self.act(obj,'confirm','sales');obj.refresh_from_db();self.assertIsNotNone(obj.confirmed_at)
        from .collaboration import change
        change(self.root,task.pk,'override',{'revision':task.revision,'decision':'reopen','body':'보완 작업이 필요하여 재개'})
        obj.refresh_from_db();self.assertIsNone(obj.confirmed_at);self.assertTrue(obj.events.filter(action='결과 확인 재요청').exists())
    def test_csrf_and_busy_handling_do_not_save_unapproved_changes(self):
        from django.test import Client,RequestFactory
        from django.db import OperationalError
        from .requests import retry_busy
        c=Client(enforce_csrf_checks=True);c.force_login(self.users['sales']);self.assertEqual(c.post('/intake/new/',self.data()).status_code,403);self.assertFalse(WorkRequest.objects.exists())
        def locked(request):raise OperationalError('database is locked')
        req=RequestFactory().post('/intake/new/');req.user=self.users['sales']
        from unittest.mock import patch
        with patch('tasks.requests.sleep') as sleeper:r=retry_busy(locked)(req)
        self.assertEqual(r.status_code,503);self.assertEqual(sleeper.call_count,2)

    def test_pre_upgrade_snapshot_preserves_source_and_contents(self):
        import importlib,sqlite3,tempfile
        from pathlib import Path
        module=importlib.import_module('tasks.migrations.0029_work_requests_and_resource_allocation')
        Path('.tmp').mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=Path('.tmp').resolve()) as directory:
            db=Path(directory)/'source.sqlite3';source=sqlite3.connect(db);source.execute('create table sample (value text)');source.execute('insert into sample values (?)',('original',));source.commit();source.close()
            target=module.snapshot_sqlite_database(str(db),Path(directory)/'backups')
            with sqlite3.connect(target) as backup:self.assertEqual(backup.execute('select value from sample').fetchone()[0],'original');self.assertEqual(backup.execute('PRAGMA integrity_check').fetchone()[0],'ok')
            with sqlite3.connect(db) as original:self.assertEqual(original.execute('select count(*) from sample').fetchone()[0],1)
