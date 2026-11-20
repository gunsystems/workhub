from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import unquote
from django.contrib.auth import get_user_model
from django.test import TestCase, SimpleTestCase, override_settings
from django.utils import timezone
from .models import DocumentRollout, Department, Employee, DocumentPermissionLease
from .document_rollout import prepare, advance, steps, no_delete_permissions, DELETE_BITS, set_acl
from .document_structure import fixed_folders
from .sharepoint_probe import SharePointProbeError

SITE='https://example.sharepoint.com/sites/documents'


def acl_row(pid,rid,kind):
    return {'id':pid,'type':8,'name':str(pid),'email':'','roles':[(rid,kind)],
            'protected':pid==3,'limited':False}


class FakeApi:
    def __init__(self):
        self.config=SimpleNamespace(site_url=SITE)
        self.connection=SimpleNamespace(email='lee@example.test')
        self.identity={'Id':6}
        self.group_names={3:'문서함 관리자',4:'문서함 전체 열람자',5:'문서함 전체 편집자',
                          19:'전 직원',12:'팀 · 보안기술팀'}
        self.people={3:[{'id':6,'name':'대표','type':1,'email':'lee@example.test','login':'lee','site_admin':True}],
                     4:[],5:[],19:[],12:[]}
        self.role_rows=[{'Id':100+k,'Name':n,'RoleTypeKind':k,'BasePermissions':{'Low':str(v),'High':'0'}}
                       for k,n,v in [(2,'Read',1),(3,'Contribute',511),(5,'Full Control',0xffffffff)]]
        self.libraries=[{'id':'documents','name':'문서'},{'id':'legacy','name':'전사 공통'}]
        self.rows={};self.acls={};self.writes=[]
        self.docs=SimpleNamespace(libraries=self.libraries,token='test-only',children=self.children,validate_item=lambda r,d:None)
        for d in ('documents','legacy'):
            self.rows[d,'root']={'id':'root','name':'root','root':{},'folder':{},'parentReference':{'id':'','driveId':d}}
        self.rows['documents','legacy-file']={'id':'legacy-file','name':'기존업무.txt','file':{},'parentReference':{'id':'root','driveId':'documents'}}
        self.base={'unique':True,'rows':[acl_row(3,105,5),acl_row(4,102,2),acl_row(5,103,3)]}
        self.resource=''

    def groups(self): return [{'id':pid,'name':name} for pid,name in self.group_names.items()]
    def members(self,pid): return deepcopy(self.people[pid])
    def create_group(self,name):
        pid=max(self.group_names)+1;self.group_names[pid]=name;self.people[pid]=[]
        self.writes.append(('group',name));return pid
    def group_change(self,pid,before,add,remove,observe):
        self.people[pid]=deepcopy(self.people[3]);self.writes.append(('member',pid));return self.members(pid)
    def children(self,drive,parent=''):
        return deepcopy([r for (d,i),r in self.rows.items() if d==drive and i!='root' and r['parentReference']['id']==(parent or 'root')])
    def bind(self,drive,item='root'):
        if drive not in {l['id'] for l in self.libraries}:raise AssertionError('cross-drive')
        self.resource=(drive,item)
        self.list_id='11111111-1111-1111-1111-111111111111' if drive=='common' else '22222222-2222-2222-2222-222222222222'
        return deepcopy(self.rows[drive,item])
    def snapshot(self): return deepcopy(self.base)
    def state(self):
        d,i=self.resource
        own=self.acls.get((d,i))
        if own:return deepcopy(own)
        parent=self.rows[d,i]['parentReference']['id']
        inherited=self.acls.get((d,parent),self.base)
        result=deepcopy(inherited);result['unique']=False
        result['source']={'name':'기본','item':'root','drive':d,'resource':'site'}
        return result
    def change(self,before,action,changes,observe):
        assert action=='break'
        self.acls[self.resource]=deepcopy(before);self.acls[self.resource]['unique']=True
        self.writes.append(('break',self.resource));return self.state()
    def mutate(self,before,suffix,observe):
        import re
        pid,rid=map(int,re.search(r'principalid=(\d+),roledefid=(\d+)',suffix).groups())
        value=self.acls[self.resource]
        row=next((r for r in value['rows'] if r['id']==pid),None)
        if 'addroleassignment' in suffix:
            kind=next(int(r['RoleTypeKind']) for r in self.role_rows if int(r['Id'])==rid)
            if row:row['roles'].append((rid,kind))
            else:value['rows'].append(acl_row(pid,rid,kind))
        else:
            row['roles']=[p for p in row['roles'] if p[0]!=rid]
            if not row['roles']:value['rows'].remove(row)
        self.writes.append(('acl',self.resource));return self.state()
    def call(self,path,method='GET',data=None):
        if path.startswith('roledefinitions'):
            if method=='POST':
                self.role_rows.append({'Id':500,'Name':data['Name'],'RoleTypeKind':0,'BasePermissions':deepcopy(data['BasePermissions'])})
                self.writes.append(('role',500))
                return {'Id':500}
            return {'value':deepcopy(self.role_rows)}
        if path=='lists' and method=='POST':
            self.libraries.append({'id':'common','name':'전사공통'})
            self.rows['common','root']={'id':'root','name':'root','root':{},'folder':{},'parentReference':{'id':'','driveId':'common'}}
            self.writes.append(('library','common'))
            return {'Id':'11111111-1111-1111-1111-111111111111'}
        raise AssertionError(path)
    def graph_write(self,url,token,method,data):
        assert method=='POST' and data['@microsoft.graph.conflictBehavior']=='fail'
        parts=unquote(url).split('/');drive=parts[parts.index('drives')+1]
        parent=parts[parts.index('items')+1] if 'items' in parts else 'root'
        item='new'+str(len(self.rows))
        row={'id':item,'name':data['name'],'folder':{},'parentReference':{'id':parent,'driveId':drive}}
        self.rows[drive,item]=row;self.writes.append(('folder',drive,item))
        return deepcopy(row)


class RoleMaskTests(SimpleTestCase):
    def test_clone_removes_only_file_and_version_deletion(self):
        result=no_delete_permissions({'BasePermissions':{'Low':'511','High':'17'}})
        self.assertEqual(result,{'Low':str(511 & ~DELETE_BITS),'High':'17'})
        self.assertEqual(int(result['Low']) & 7,7)
        self.assertEqual(sum(a=='folder' for a,_ in steps()),51)
        self.assertEqual(sum(a=='acl' for a,_ in steps()),6)


@override_settings(WORKHUB_REQUIRE_HIWORKS=False,WORKHUB_SCHEDULER_ENABLED=False)
class RolloutTests(TestCase):
    def setUp(self):
        self.user=get_user_model().objects.create_superuser('rollout','lee@example.test','unused')
        dept=Department.objects.create(name='경영관리')
        Employee.objects.create(user=self.user,name='대표',position='executive',department=dept)
        self.api=FakeApi()
        self.scope=patch('tasks.document_rollout.scope',return_value=('scope',SITE));self.scope.start();self.addCleanup(self.scope.stop)
        self.writer=patch('tasks.document_rollout.graph_write',side_effect=self.api.graph_write);self.writer.start();self.addCleanup(self.writer.stop)

    def test_full_rollout_creates_exact_tree_and_only_star_acls_preserving_legacy(self):
        old=deepcopy(self.api.rows)
        run=prepare(self.user,self.api)
        self.assertFalse(self.api.writes)
        while run.state!='done':run=advance(self.user,run.pk,run.revision,self.api)
        self.assertEqual(run.cursor,len(steps()))
        self.assertEqual(sum(a[0]=='folder' for a in self.api.writes),51)
        self.assertEqual(sum(a[0]=='break' for a in self.api.writes),6)
        for key,row in old.items():self.assertEqual(self.api.rows[key],row)
        self.assertNotIn(('documents','root'),self.api.acls)
        self.assertNotIn(('legacy','root'),self.api.acls)
        for spec in fixed_folders():
            owned=run.data['owned'][spec.display]
            self.assertEqual((owned['drive'],owned['item']) in self.api.acls,spec.unique)
        writes=len(self.api.writes)
        self.assertEqual(prepare(self.user,self.api).pk,run.pk)
        with self.assertRaises(SharePointProbeError):advance(self.user,run.pk,run.revision,self.api)
        self.assertEqual(len(self.api.writes),writes)

    def test_existing_path_blocks_before_any_write(self):
        self.api.rows['documents','collision']={'id':'collision','name':'01_영업자료','folder':{},'parentReference':{'id':'root','driveId':'documents'}}
        with self.assertRaises(SharePointProbeError):prepare(self.user,self.api)
        self.assertFalse(self.api.writes);self.assertFalse(DocumentRollout.objects.exists())

    def test_stale_revision_and_other_user_cannot_execute(self):
        run=prepare(self.user,self.api);run=advance(self.user,run.pk,0,self.api)
        count=len(self.api.writes)
        with self.assertRaises(SharePointProbeError):advance(self.user,run.pk,0,self.api)
        other=get_user_model().objects.create_superuser('other','other@example.test','unused')
        with self.assertRaises(DocumentRollout.DoesNotExist):advance(other,run.pk,run.revision,self.api)
        self.assertEqual(len(self.api.writes),count)

    def test_uncertain_write_never_retries(self):
        run=prepare(self.user,self.api)
        with patch.object(self.api,'create_group',side_effect=SharePointProbeError('timeout')):
            with self.assertRaises(SharePointProbeError):advance(self.user,run.pk,0,self.api)
        run.refresh_from_db()
        self.assertEqual(run.state,'attention');self.assertTrue(run.pending)
        with self.assertRaises(SharePointProbeError):advance(self.user,run.pk,0,self.api)
        self.assertFalse(self.api.writes)

    def test_changed_membership_stops_before_first_acl(self):
        run=prepare(self.user,self.api)
        while run.data['steps'][run.cursor][0]!='acl':run=advance(self.user,run.pk,run.revision,self.api)
        self.api.people[12].append({'id':99,'name':'외부 변경'})
        before=len(self.api.writes)
        with self.assertRaises(SharePointProbeError):advance(self.user,run.pk,run.revision,self.api)
        self.assertEqual(len(self.api.writes),before)

    def test_acl_refuses_non_owned_and_non_star_paths(self):
        run=prepare(self.user,self.api)
        with self.assertRaises(SharePointProbeError):set_acl(self.api,run.data,'documents/기존업무')
        self.assertFalse(self.api.writes)

    def test_non_representative_admin_cannot_self_enroll_as_executive(self):
        Employee.objects.filter(user=self.user).update(position='member')
        with self.assertRaises(SharePointProbeError):prepare(self.user,self.api)
        self.assertFalse(self.api.writes)

    def test_library_creation_stops_on_changed_site_acl(self):
        run=prepare(self.user,self.api)
        while run.data['steps'][run.cursor][0]!='library':run=advance(self.user,run.pk,run.revision,self.api)
        self.api.base['rows'].append(acl_row(12,102,2))
        before=len(self.api.writes)
        with self.assertRaises(SharePointProbeError):advance(self.user,run.pk,run.revision,self.api)
        self.assertEqual(len(self.api.writes),before)
        self.assertFalse(any(l['name']=='전사공통' for l in self.api.libraries))

    def test_http_detail_is_scoped_and_steps_require_admin_post(self):
        run=prepare(self.user,self.api);self.client.force_login(self.user)
        with patch('tasks.document_structure_views.scope',return_value=('scope',SITE)):
            self.assertEqual(self.client.get(f'/work/shared-documents/structure/runs/{run.pk}/').status_code,200)
            self.assertEqual(self.client.get(f'/work/shared-documents/structure/runs/{run.pk}/step/').status_code,405)
            self.assertEqual(self.client.post(f'/work/shared-documents/structure/runs/{run.pk}/step/',{'revision':0}).status_code,409)
            self.assertFalse(self.api.writes)
        with patch('tasks.document_structure_views.scope',return_value=('different',SITE)):
            self.assertEqual(self.client.get(f'/work/shared-documents/structure/runs/{run.pk}/').status_code,404)
        other=get_user_model().objects.create_user('staff',email='staff@example.test')
        from accounts.models import AccessProfile
        AccessProfile.objects.create(user=other,email=other.email,verified_at=timezone.now(),require_mfa=False)
        self.client.force_login(other)
        self.assertEqual(self.client.post('/work/shared-documents/structure/prepare/').status_code,403)
        self.assertEqual(self.client.post(f'/work/shared-documents/structure/runs/{run.pk}/step/').status_code,403)

    def test_csrf_missing_does_not_reach_prepare(self):
        from django.test import Client
        client=Client(enforce_csrf_checks=True);client.force_login(self.user)
        with patch('tasks.document_rollout.prepare') as work:
            self.assertEqual(client.post('/work/shared-documents/structure/prepare/').status_code,403)
            work.assert_not_called()

    def test_site_wide_lease_prevents_conflicting_group_or_ancestor_changes(self):
        from .document_permission_api import digest
        from datetime import timedelta
        run=prepare(self.user,self.api)
        DocumentPermissionLease.objects.create(target=digest(['document-permission-site',SITE]),
            expires_at=timezone.now()+timedelta(minutes=10))
        with self.assertRaises(SharePointProbeError):advance(self.user,run.pk,0,self.api)
        run.refresh_from_db();self.assertEqual(run.state,'prepared');self.assertEqual(run.pending,'')
        self.assertFalse(self.api.writes)

    def test_limited_access_does_not_change_content_acl_signature(self):
        from .document_rollout import acl_signature
        before=acl_signature(self.api.base)
        row=acl_row(99,101,1);row['limited']=True
        self.api.base['rows'].append(row)
        self.assertEqual(acl_signature(self.api.base),before)
