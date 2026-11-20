from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError, PermissionDenied
from django.test import TestCase, override_settings
from django.utils import timezone
from . import test_document_rollout as baseline
from .models import Project,Employee,DocumentProject,DocumentPermissionLease,SharedDocument,SharedDocumentLink
from .document_projects import prepare,advance,prepare_close,manifest,content_signature
from .document_rollout import prepare as initial_prepare,advance as initial_advance
from .document_permission_api import digest
from .sharepoint_probe import SharePointProbeError


class ProjectFake(baseline.FakeApi):
    def __init__(self):
        super().__init__()
        self.libraries.append({'id':'projects','name':'프로젝트 공동작업'})
        self.rows['projects','root']={'id':'root','name':'root','root':{},'folder':{},'parentReference':{'id':'','driveId':'projects'}}
        self.users={'lee@example.test':deepcopy(self.people[3][0]),'kim@example.test':
            {'id':10,'name':'김예린','type':1,'email':'kim@example.test','login':'kim','site_admin':False}}
        self.version_rows={};self.queued=0;self.waiting=False;self.fail_versions=False

    def bind(self,drive,item='root'):
        super().bind(drive,item)
        return self.graph(drive,item)

    def graph(self,drive,item,suffix=''):
        row=deepcopy(self.rows[drive,item]);row.setdefault('size',0);row.setdefault('eTag','etag-'+item)
        row['webUrl']=baseline.SITE+'/'+drive+'/'+item
        row['parentReference']['driveId']=drive
        return row

    def group_change(self,gid,before,add,remove,observe):
        self.people[gid]=[deepcopy(self.users[email]) for email in add]
        self.writes.append(('member',gid));return self.members(gid)

    def versions(self,drive,item):
        rows=deepcopy(self.version_rows.get((drive,item),[{'id':'1.0','size':3},{'id':'2.0','size':4}]))
        return rows[:-1] if self.fail_versions and drive=='documents' else rows

    def start_move(self,source,destination,etag):
        self.queued+=1
        src_drive,src=source.split('/')[-2:];dest_drive,dest=destination.split('/')[-2:]
        todo=[src];mapping={};all_items=[]
        while todo:
            item=todo.pop(0);all_items.append(item)
            todo.extend(r['id'] for r in self.children(src_drive,item))
        for item in all_items:
            row=deepcopy(self.rows[src_drive,item]);new='moved-'+item;mapping[item]=new
            row['id']=new;row['parentReference']['driveId']=dest_drive
            row['parentReference']['id']=dest if item==src else mapping[row['parentReference']['id']]
            self.rows[dest_drive,new]=row
            if 'file' in row:self.version_rows[dest_drive,new]=self.versions(src_drive,item)
        if (src_drive,src) in self.acls:self.acls[dest_drive,mapping[src]]=deepcopy(self.acls[src_drive,src])
        for item in all_items:self.rows.pop((src_drive,item));self.acls.pop((src_drive,item),None)
        return 'encrypted-test-only'

    def move_done(self,encrypted):return not self.waiting


@override_settings(WORKHUB_REQUIRE_HIWORKS=False,WORKHUB_SCHEDULER_ENABLED=False)
class ProjectTests(TestCase):
    def setUp(self):
        baseline.RolloutTests.setUp(self)
        self.api=ProjectFake()
        # baseline setup's writer resolves to the old object; replace explicitly.
        self.writer.stop()
        self.writer=patch('tasks.document_rollout.graph_write',side_effect=self.api.graph_write)
        self.writer.start();self.addCleanup(self.writer.stop)
        for name,value in [('scope',lambda user:('scope',baseline.SITE)),('graph_write',self.api.graph_write)]:
            p=patch('tasks.document_projects.'+name,side_effect=value);p.start();self.addCleanup(p.stop)
        p=patch('tasks.document_vendors.scope',return_value=('scope',baseline.SITE));p.start();self.addCleanup(p.stop)
        run=initial_prepare(self.user,self.api)
        while run.state!='done':run=initial_advance(self.user,run.pk,run.revision,self.api)
        self.owner=Employee.objects.get(user=self.user)
        kim=get_user_model().objects.create_user('kim','kim@example.test','unused')
        self.kim=Employee.objects.create(user=kim,name='김예린',department=self.owner.department)
        today=timezone.localdate()
        self.project=Project.objects.create(name='테스트 프로젝트',owner=self.owner,department=self.owner.department,start_date=today,due_date=today)
        self.legacy=deepcopy(self.api.rows)

    def prepare(self,**overrides):
        values={'year':2026,'customer':'고객','vendor':'Tenable','kind':'PoC','internal':''};values.update(overrides)
        return prepare(self.user,self.project,values,[self.owner,self.kim],self.api)

    def finish(self,row):
        for _ in range(20):
            row,waiting=advance(self.user,row.pk,row.revision,self.api)
            if row.state in ('active','closed'):return row
        self.fail('did not finish')

    def file(self,row):
        child=row.data['records']['01_영업']['item']
        self.api.rows['projects','file']={'id':'file','name':'제안서.docx','size':4,'file':{'hashes':{'quickXorHash':'value'}},
            'parentReference':{'id':child,'driveId':'projects'}}
        doc=SharedDocument.objects.create(site_url=baseline.SITE,drive_id='projects',item_id='file')
        return SharedDocumentLink.objects.create(document=doc,project=self.project,creator=self.user)

    def test_create_move_preserves_versions_work_links_and_legacy(self):
        row=self.finish(self.prepare());link=self.file(row)
        before=manifest(self.api,row.data['records']['root'])
        row=prepare_close(self.user,row.pk,self.api);row=self.finish(row)
        self.assertEqual(row.state,'closed');self.assertEqual(self.api.queued,1)
        self.assertNotIn('move_job',row.data)
        self.project.refresh_from_db();self.assertEqual(self.project.document_status,'closed')
        link.refresh_from_db();link.document.refresh_from_db()
        self.assertEqual(link.document.item_id,'moved-file');self.assertEqual(link.document.drive_id,'documents')
        self.assertEqual(content_signature(before),content_signature(manifest(self.api,row.data['records']['root'],archived=True)))
        for key,value in self.legacy.items():self.assertEqual(self.api.rows[key],value)
        self.assertEqual({p['id'] for p in self.api.members(row.data['groups']['participants']['id'])},{6,10})

    def test_no_existing_name_adoption_or_duplicate_project(self):
        row=self.prepare()
        with self.assertRaises(ValidationError):self.prepare()
        row=self.finish(row)
        with self.assertRaises(DocumentProject.DoesNotExist):prepare_close(self.user,999,self.api)

    def test_partial_write_is_journaled_and_never_retried(self):
        row=self.prepare()
        with patch.object(self.api,'create_group',side_effect=SharePointProbeError('unknown')):
            with self.assertRaises(SharePointProbeError):advance(self.user,row.pk,row.revision,self.api)
        row.refresh_from_db();self.assertEqual(row.state,'attention');self.assertEqual(row.pending,'group')
        with self.assertRaises(SharePointProbeError):advance(self.user,row.pk,row.revision,self.api)

    def test_stale_revision_and_site_lock_reject_before_write(self):
        row=self.prepare();before=len(self.api.writes)
        with self.assertRaises(SharePointProbeError):advance(self.user,row.pk,99,self.api)
        DocumentPermissionLease.objects.create(target=digest(['document-permission-site',baseline.SITE]),expires_at=timezone.now()+timezone.timedelta(minutes=1))
        with self.assertRaises(SharePointProbeError):advance(self.user,row.pk,row.revision,self.api)
        self.assertEqual(len(self.api.writes),before)

    def test_poll_does_not_resubmit_move(self):
        row=self.finish(self.prepare());row=prepare_close(self.user,row.pk,self.api)
        for _ in range(3):row,_=advance(self.user,row.pk,row.revision,self.api)
        self.api.waiting=True
        for _ in range(2):row,waiting=advance(self.user,row.pk,row.revision,self.api);self.assertTrue(waiting)
        self.assertEqual(self.api.queued,1);self.api.waiting=False;row=self.finish(row)
        self.assertEqual(row.state,'closed')

    def test_missing_versions_never_mark_project_closed(self):
        row=self.finish(self.prepare());self.file(row);row=prepare_close(self.user,row.pk,self.api)
        for _ in range(4):row,_=advance(self.user,row.pk,row.revision,self.api)
        self.api.fail_versions=True
        with self.assertRaises(SharePointProbeError):advance(self.user,row.pk,row.revision,self.api)
        row.refresh_from_db();self.assertEqual(row.state,'attention');self.assertIsNone(row.closed_at)
        self.project.refresh_from_db();self.assertEqual(self.project.document_status,'active')

    def test_child_unique_permissions_and_inactive_member_block(self):
        row=self.finish(self.prepare());child=row.data['records']['02_기술']['item']
        self.api.acls['projects',child]=deepcopy(self.api.base)
        with self.assertRaises(SharePointProbeError):prepare_close(self.user,row.pk,self.api)
        with self.assertRaises(PermissionDenied):advance(self.kim.user,row.pk,row.revision,self.api)

    def test_http_visibility_and_post_only(self):
        row=self.finish(self.prepare());self.client.force_login(self.kim.user)
        self.assertEqual(self.client.get('/work/shared-documents/projects/').status_code,200)
        self.assertEqual(self.client.get(f'/work/shared-documents/projects/{row.pk}/').status_code,200)
        self.assertEqual(self.client.get('/work/shared-documents/projects/new/').status_code,403)
        self.assertEqual(self.client.post(f'/work/shared-documents/projects/{row.pk}/close/',{'reviewed':'yes'}).status_code,403)
        self.assertEqual(self.client.get(f'/work/shared-documents/projects/{row.pk}/step/').status_code,403)
        outsider=get_user_model().objects.create_user('other','other@example.test','unused')
        Employee.objects.create(user=outsider,name='다른직원',department=self.owner.department)
        self.client.force_login(outsider)
        self.assertEqual(self.client.get(f'/work/shared-documents/projects/{row.pk}/').status_code,404)

    def test_creation_http_form_internal_and_customer(self):
        self.client.force_login(self.user)
        with patch('tasks.document_project_views.scope',return_value=('scope',baseline.SITE)), \
                patch('tasks.document_projects.ProjectAPI',return_value=self.api):
            response=self.client.post('/work/shared-documents/projects/new/',{'project':self.project.pk,'year':2026,
                'mode':'internal','internal':'검증','participants':[self.owner.pk]})
        self.assertEqual(response.status_code,302)
        self.assertTrue(DocumentProject.objects.filter(project=self.project,name='2026_내부_검증').exists())

    def test_external_io_is_outside_additional_sql_transaction(self):
        from django.db import connection
        from . import document_projects
        row=self.prepare();depth=len(connection.savepoint_ids);original=document_projects.perform
        def check(*args):
            self.assertEqual(len(connection.savepoint_ids),depth)
            return original(*args)
        with patch('tasks.document_projects.perform',side_effect=check):
            advance(self.user,row.pk,row.revision,self.api)

    def test_acl_reconciliation_only_reads_completed_result(self):
        from .document_projects import reconcile_acl
        row=self.prepare()
        for _ in range(4):row,_=advance(self.user,row.pk,row.revision,self.api)
        # Simulate lost local acknowledgement after the fourth provider step.
        row.state='attention';row.pending='root_acl';row.cursor=3;row.save()
        before=len(self.api.writes)
        row=reconcile_acl(self.user,row.pk,self.api)
        self.assertEqual(row.cursor,4);self.assertEqual(row.state,'prepared')
        self.assertEqual(len(self.api.writes),before)

    def test_project_detail_displays_existing_document_folder(self):
        row=self.finish(self.prepare())
        self.client.force_login(self.user)
        response=self.client.get(f'/projects/{self.project.pk}/')
        self.assertContains(response,f'/work/shared-documents/projects/{row.pk}/')
        self.assertNotContains(response,'프로젝트 폴더 만들기')
