from copy import deepcopy
from unittest.mock import Mock, patch
from datetime import timedelta
from django.contrib.auth import get_user_model
from django.test import TestCase, Client
from django.utils import timezone
from .models import Department, Employee, DocumentAccessGroup, DocumentPermissionLease, SystemAudit
from .document_permission_api import digest
from .group_permission_api import GroupPermissionClient, entries
from .sharepoint_probe import SharePointProbeError

def row(pid, kind=2, ptype=8, protected=False):
    return {'id':pid,'name':str(pid),'email':'','type':ptype,'roles':[(100+kind,kind)],'protected':protected,'limited':kind==1}

def state(unique=True):
    return {'unique':unique,'rows':[row(1,5,protected=True),row(2),row(3,ptype=1)],
            'source':{'resource':'safe','name':'상위','drive':'drive','item':'parent'}}

class Mutable(GroupPermissionClient):
    def __init__(self):
        self.value=state(); self.resource='safe'; self.operations=[]; self.identity={'Id':1}
    def state(self): return deepcopy(self.value)
    def roles(self): return {'read':102,'write':103,'manage':105}
    def parent_state(self): return state(False)
    def groups(self): return [{'id':2},{'id':4}]
    def call(self,suffix,method='GET',data=None):
        import re
        self.operations.append(suffix)
        if 'breakroleinheritance' in suffix: self.value['unique']=True
        elif 'resetroleinheritance' in suffix: self.value['unique']=False
        elif 'roleassignment(' in suffix:
            pid,rid=map(int,re.search(r'principalid=(\d+),roledefid=(\d+)',suffix).groups())
            person=next((r for r in self.value['rows'] if r['id']==pid),None)
            if 'addroleassignment' in suffix:
                if person is None:
                    person=row(pid); person['roles']=[]; self.value['rows'].append(person)
                person['roles'].append((rid,rid-100)); person['roles'].sort()
            else:
                person['roles']=[r for r in person['roles'] if r[0]!=rid]
                if not person['roles']: self.value['rows'].remove(person)
            self.value['rows'].sort(key=lambda r:r['id'])
        return {}

class GroupApiTests(TestCase):
    def test_combined_save_copies_then_changes_only_selected_group(self):
        api=Mutable(); api.value['unique']=False; before=api.state()
        after=api.change(before,'configure_groups',[{'id':4,'role':'write'}],Mock())
        self.assertTrue(after['unique'])
        self.assertIn('breakroleinheritance(copyRoleAssignments=true,clearSubscopes=false)',api.operations[0])
        self.assertIn('addroleassignment',api.operations[1])
        self.assertEqual(after['rows'][:3],before['rows'])

    def test_combined_invalid_or_empty_delta_never_breaks_inheritance(self):
        for changes in ([],[{'id':999,'role':'write'}],[{'id':1,'role':'none'}],
                        [{'id':4,'role':'write'},{'id':4,'role':'read'}]):
            api=Mutable(); api.value['unique']=False
            with self.assertRaises(SharePointProbeError):
                api.change(api.state(),'configure_groups',changes,Mock())
            self.assertEqual(api.operations,[])

    def test_combined_unique_save_does_not_copy_inheritance(self):
        api=Mutable(); api.change(api.state(),'configure_groups',[{'id':4,'role':'read'}],Mock())
        self.assertEqual(len(api.operations),1)
        self.assertIn('addroleassignment',api.operations[0])

    def test_combined_copy_failure_stops_before_grant(self):
        api=Mutable(); api.value['unique']=False
        original=api.call
        def lose_grant(*args,**kwargs):
            result=original(*args,**kwargs)
            api.value['rows']=[r for r in api.value['rows'] if r['id']!=2]
            return result
        api.call=lose_grant
        with self.assertRaises(SharePointProbeError):
            api.change(api.state(),'configure_groups',[{'id':4,'role':'read'}],Mock())
        self.assertEqual(len(api.operations),1)

    def test_inheritance_copy_allows_only_sharepoint_traversal_metadata_changes(self):
        api = Mutable(); api.value['unique'] = False
        api.value['rows'].append(row(9, kind=1))
        before = api.state()
        original = api.call
        def copy_without_limited(suffix, method='GET', data=None):
            result = original(suffix, method, data)
            if 'breakroleinheritance' in suffix:
                api.value['rows'] = [r for r in api.value['rows'] if not r['limited']]
                api.value['rows'][0]['email'] = 'scope-metadata@example.invalid'
            return result
        api.call = copy_without_limited
        self.assertTrue(api.change(before, 'break', [], Mock())['unique'])

    def test_inheritance_copy_rejects_lost_real_grant(self):
        api = Mutable(); api.value['unique'] = False; before = api.state()
        original = api.call
        def lose_grant(suffix, method='GET', data=None):
            result = original(suffix, method, data)
            if 'breakroleinheritance' in suffix:
                api.value['rows'] = [r for r in api.value['rows'] if r['id'] != 2]
            return result
        api.call = lose_grant
        with self.assertRaises(SharePointProbeError):
            api.change(before, 'break', [], Mock())

    def test_delta_keeps_unselected_group_personal_access_and_owner(self):
        api=Mutable(); result=api.change(api.state(),'groups',[{'id':4,'role':'write'}],Mock())
        self.assertEqual([r['id'] for r in result['rows']],[1,2,3,4]); self.assertEqual(len(api.operations),1)
    def test_new_role_added_before_old_removed(self):
        api=Mutable(); result=api.change(api.state(),'groups',[{'id':2,'role':'write'}],Mock())
        self.assertIn('addroleassignment',api.operations[0]); self.assertIn('removeroleassignment',api.operations[1])
        self.assertEqual(result['rows'][1]['roles'],[(103,3)])
    def test_remove_only_named_group(self):
        api=Mutable(); result=api.change(api.state(),'groups',[{'id':2,'role':'none'}],Mock())
        self.assertEqual([r['id'] for r in result['rows']],[1,3])
    def test_inherited_save_never_breaks_implicitly(self):
        api=Mutable(); api.value['unique']=False
        with self.assertRaises(SharePointProbeError): api.change(api.state(),'groups',[{'id':2,'role':'write'}],Mock())
        self.assertEqual(api.operations,[])
    def test_explicit_break_copies_and_keeps_child_scopes(self):
        api=Mutable(); api.value['unique']=False; before=api.state()
        result=api.change(before,'break',[],Mock())
        self.assertTrue(result['unique']); self.assertEqual(before['rows'],result['rows'])
        self.assertEqual(api.operations,['safe/breakroleinheritance(copyRoleAssignments=true,clearSubscopes=false)'])
    def test_explicit_reset(self):
        api=Mutable(); self.assertFalse(api.change(api.state(),'reset',[],Mock(),parent=api.parent_state())['unique'])
        self.assertEqual(api.operations,['safe/resetroleinheritance()'])
    def test_stale_state_blocks_write(self):
        api=Mutable(); before=api.state(); api.value['rows'].append(row(9))
        with self.assertRaises(SharePointProbeError): api.change(before,'groups',[{'id':2,'role':'write'}],Mock())
        self.assertEqual(api.operations,[])
    def test_owner_and_person_cannot_be_submitted_as_group(self):
        for pid in (1,3):
            api=Mutable()
            with self.assertRaises(SharePointProbeError): api.change(api.state(),'groups',[{'id':pid,'role':'none'}],Mock())
            self.assertEqual(api.operations,[])
    def test_silent_write_failure_is_detected(self):
        api=Mutable(); api.call=Mock(return_value={})
        with self.assertRaises(SharePointProbeError): api.change(api.state(),'groups',[{'id':2,'role':'write'}],Mock())
        self.assertEqual(api.call.call_count,1)
    def test_network_failure_never_retried(self):
        api=Mutable(); api.call=Mock(side_effect=SharePointProbeError('중단'))
        with self.assertRaises(SharePointProbeError): api.change(api.state(),'groups',[{'id':2,'role':'write'}],Mock())
        self.assertEqual(api.call.call_count,1)
    def test_incomplete_group_list_not_treated_as_complete(self):
        for key in ('@odata.nextLink','odata.nextLink','__next'):
            with self.assertRaises(SharePointProbeError): entries({'value':[],key:'next'})

class AncestorTests(TestCase):
    def api(self):
        api=object.__new__(GroupPermissionClient); api.list_id='aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa'
        api.resource=f"lists(guid'{api.list_id}')/items(3)"; api.drive='drive'; api.item_id='child'
        api.target_row={'name':'하위','parentReference':{'id':'parent'}}; api.docs=Mock()
        api.docs.item.return_value={'name':'상위','parentReference':{'id':'root-id'}}
        return api
    def test_nearest_unique_ancestor_uses_rest(self):
        api=self.api(); api.call=Mock(side_effect=[{'HasUniqueRoleAssignments':False},{'HasUniqueRoleAssignments':True}])
        api.snapshot=Mock(return_value={'unique':True,'rows':[row(1,5,protected=True)]})
        with patch('tasks.group_permission_api._json_request',return_value={'sharepointIds':{'listId':api.list_id,'listItemId':'2'}}): result=api.state()
        self.assertFalse(result['unique']); self.assertEqual(result['source']['item'],'parent')
        self.assertTrue(api.resource.endswith('items(3)'))
    def test_root_uses_configured_web_no_parent_site(self):
        api=self.api(); api.resource=f"lists(guid'{api.list_id}')"; api.item_id='root'
        api.call=Mock(side_effect=[{'HasUniqueRoleAssignments':False},{'HasUniqueRoleAssignments':True,'Title':'전용 사이트'}])
        api.snapshot=Mock(return_value={'unique':True,'rows':[row(1,5,protected=True)]})
        self.assertEqual(api.state()['source']['resource'],'')
    def test_foreign_list_ancestor_rejected(self):
        api=self.api(); api.call=Mock(return_value={'HasUniqueRoleAssignments':False})
        with patch('tasks.group_permission_api._json_request',return_value={'sharepointIds':{'listId':'other','listItemId':2}}):
            with self.assertRaises(SharePointProbeError): api.state()

class MembershipTests(TestCase):
    def test_registered_employee_before_first_login_can_be_selected(self):
        from .document_permissions import employees
        from .models import HiworksLink
        department=Department.objects.create(name='기술팀')
        staff=Employee.objects.create(name='등록 직원',department=department)
        HiworksLink.objects.create(employee=staff,user_id='registered',remote_id='registered',email='registered@gunsystems.co.kr')
        self.assertEqual(list(employees().values_list('pk','email')),[(staff.pk,'registered@gunsystems.co.kr')])
        staff.is_active=False; staff.save()
        self.assertFalse(employees().exists())

    def test_default_groups_cannot_be_enrolled_even_after_rename(self):
        api=object.__new__(GroupPermissionClient)
        api.call=Mock(side_effect=[{'Id':3},{'Id':5},{'Id':4}])
        with self.assertRaises(SharePointProbeError): api.check_membership_group(5)

    def test_group_protection_fails_closed_on_incomplete_response(self):
        api=object.__new__(GroupPermissionClient); api.call=Mock(return_value={})
        with self.assertRaises(SharePointProbeError): api.check_membership_group(12)

    def test_custom_group_may_be_enrolled_without_sharepoint_write(self):
        api=object.__new__(GroupPermissionClient)
        api.call=Mock(side_effect=[{'Id':3},{'Id':5},{'Id':4}])
        api.check_membership_group(12)
        self.assertEqual(api.call.call_count,3)
        self.assertTrue(all(len(c.args)==1 for c in api.call.call_args_list))

    def api(self):
        api=object.__new__(GroupPermissionClient); api.identity={'Id':1}
        api.members=Mock(return_value=[{'id':1,'site_admin':True},{'id':2,'site_admin':False}])
        api.call=Mock(); api.principal=Mock(return_value=3); return api
    def test_owner_not_removed(self):
        api=self.api()
        with self.assertRaises(SharePointProbeError): api.group_change(4,api.members(),[],[1],Mock())
        api.call.assert_not_called()
    def test_stale_members_block_update(self):
        api=self.api()
        with self.assertRaises(SharePointProbeError): api.group_change(4,[],['new@gunsystems.co.kr'],[],Mock())
        api.call.assert_not_called()
    def test_add_result_verified_and_observed(self):
        api=self.api(); before=api.members(); after=before+[{'id':3,'site_admin':False}]
        api.members.side_effect=[before,before,after]; api.call.return_value={'LoginName':'new-login'}
        observed=Mock(); result=api.group_change(4,before,['new@gunsystems.co.kr'],[],observed)
        self.assertEqual(result,after); self.assertEqual(observed.call_count,1)
        self.assertEqual(api.call.call_args.args[:2],('sitegroups/getbyid(4)/users','POST'))

class WorkspaceTests(TestCase):
    def setUp(self):
        users=get_user_model(); self.admin=users.objects.create_superuser('admin','admin@gunsystems.co.kr','unused')
        self.worker=users.objects.create_user('worker',email='worker@gunsystems.co.kr')
        self.staff=Employee.objects.create(user=self.worker,name='직원',department=Department.objects.create(name='기술팀'))
        self.url='/work/shared-documents/drive/folder/permissions/'; self.client.force_login(self.admin)
        self.factory=patch('tasks.group_permissions.GroupPermissionClient').start(); self.addCleanup(patch.stopall)
        self.api=self.factory.return_value; self.api.config.site_url='https://tenant.sharepoint.com/sites/work'
        self.api.connection.connected_at=timezone.now(); self.api.identity={'Id':1}
        self.api.docs.libraries=[{'id':'drive','name':'공통'}]; self.api.bind.return_value={'name':'교육자료','folder':{}}
        self.api.docs.children_page.return_value=([{'id':'child','name':'실제 폴더','folder':{}}],'',50)
        self.api.state.side_effect=lambda:state(); self.api.change.return_value=state()
        self.api.groups.return_value=[{'id':2,'name':'Workhub_기술팀','login':''}]; self.api.members.return_value=[]
        DocumentAccessGroup.objects.create(site_url=self.api.config.site_url,principal_id=2,name='Workhub_기술팀')
    def preview(self,**data):
        response=self.client.post(self.url+'review/',data or {'action':'groups','group_2':'write'})
        self.assertEqual(response.status_code,200); return response.context['nonce']
    def test_get_and_review_are_read_only(self):
        response=self.client.get(self.url); self.assertContains(response,'그룹 구성원'); self.assertContains(response,'직원별 확인')
        self.assertContains(response,'실제 폴더'); self.assertContains(response,'접근 관리'); self.assertNotContains(response,'permission-table'); self.preview(); self.api.change.assert_not_called()
    def test_fragment_has_panel_only(self):
        response=self.client.get(self.url+'?panel=1&edit=1'); self.assertContains(response,'상위 위치와 같은 접근 설정으로 되돌리기')
        self.assertNotContains(response,'permission-workspace')
    def test_editor_shows_saved_members_and_keeps_owner_separate_without_writes(self):
        self.api.groups.return_value.append({'id':1,'name':'문서함 관리자','login':''})
        previews={2:[{'id':8,'name':'김예린','kind':'person'}],1:[{'id':7,'name':'이상건','kind':'person'}]}
        with patch('tasks.permission_inventory.member_previews',return_value=(previews,timezone.now())):
            response=self.client.get(self.url+'?edit=1')
        self.assertContains(response,'김예린');self.assertContains(response,'이상건')
        self.assertContains(response,'최근 확인 구성원 · 1명');self.assertContains(response,'관리자 접근 유지')
        self.assertContains(response,'data-access-members');self.assertContains(response,'group=2')
        self.assertContains(response,'data-original="read"');self.assertNotContains(response,'name="group_1"')
        self.api.change.assert_not_called();self.api.members.assert_not_called()

    def test_empty_member_preview_is_explicit_and_does_not_mean_failed_lookup(self):
        with patch('tasks.permission_inventory.member_previews',return_value=({2:[]},timezone.now())):
            response=self.client.get(self.url+'?edit=1')
        self.assertContains(response,'최근 확인 구성원 · 0명')
        self.assertContains(response,'등록된 구성원이 없습니다.')

    def test_new_group_uses_plain_name_without_system_prefix(self):
        self.api.create_group.return_value=44
        nonce=self.preview(action='create_group',name='신규 업무 그룹')
        self.client.post(self.url+'apply/',{'nonce':nonce,'confirmed':'yes','reason':'업무 그룹'})
        self.api.create_group.assert_called_once_with('신규 업무 그룹')
        self.assertTrue(DocumentAccessGroup.objects.filter(principal_id=44,name='신규 업무 그룹').exists())

    def test_all_staff_configuration_is_reviewed_and_preserves_group_and_acl(self):
        from .models import DocumentStaffGroup
        with patch('tasks.permission_inventory.scope',return_value=('approved-scope',self.api.config.site_url)):
            nonce=self.preview(action='auto_staff',group='2')
            self.assertFalse(DocumentStaffGroup.objects.exists())
            self.client.post(self.url+'apply/',{'nonce':nonce,'confirmed':'yes','reason':'전 직원 자동 관리'})
        policy=DocumentStaffGroup.objects.get()
        self.assertTrue(policy.enabled);self.assertEqual(policy.group.principal_id,2);self.assertEqual(policy.group.name,'전 직원')
        self.api.rename_group.assert_called_once_with(2,'전 직원')
        self.api.change.assert_not_called();self.api.group_change.assert_not_called()

    def test_manual_member_edits_blocked_for_automatic_group(self):
        from .models import DocumentStaffGroup
        group=DocumentAccessGroup.objects.get(principal_id=2)
        DocumentStaffGroup.objects.create(group=group,owner=self.admin,connection_key='scope',enabled=True)
        response=self.client.get(self.url+'?tab=groups&group=2')
        self.assertContains(response,'전 직원 · 자동 관리');self.assertNotContains(response,'<h4>직원 추가</h4>',html=True)
        self.assertEqual(self.client.post(self.url+'review/',{'action':'members','group':'2','add':self.staff.pk}).status_code,302)
        self.api.group_change.assert_not_called()
    def test_folder_list_does_not_mix_in_groups_or_fetch_acl(self):
        response=self.client.get(self.url)
        self.assertContains(response,'실제 폴더 목록')
        self.assertContains(response,'실제 폴더 접근 관리')
        self.assertNotContains(response,'Workhub_기술팀')
        self.api.state.assert_not_called(); self.api.groups.assert_not_called()

    def test_unused_group_appears_only_in_add_picker(self):
        self.api.groups.return_value.append({'id':4,'name':'새 업무 그룹','login':''})
        response=self.client.get(self.url+'?edit=1')
        self.assertContains(response,'Workhub_기술팀')
        self.assertContains(response,'<option value="4">새 업무 그룹</option>',html=True)
        self.assertNotContains(response,'name="group_4"')
        self.assertContains(response,'name="group_2"')
    def test_explicit_apply_one_use_and_reason(self):
        nonce=self.preview(); self.client.post(self.url+'apply/',{'nonce':nonce,'confirmed':'yes','reason':'부서 공유'})
        self.assertEqual(self.api.change.call_count,1); self.assertEqual(self.api.change.call_args.args[2],[{'id':2,'role':'write'}])
        self.client.post(self.url+'apply/',{'nonce':nonce,'confirmed':'yes','reason':'재전송'}); self.assertEqual(self.api.change.call_count,1)
    def test_missing_confirmation_or_reason(self):
        for data in ({'reason':'사유'},{'confirmed':'yes'}): self.client.post(self.url+'apply/',{'nonce':self.preview(),**data})
        self.api.change.assert_not_called()
    def test_inherited_review_cannot_break(self):
        self.api.state.side_effect=lambda:state(False)
        self.assertEqual(self.client.post(self.url+'review/',{'action':'groups','group_2':'write'}).status_code,302)
        self.api.change.assert_not_called()
    def test_inherited_folder_can_be_edited_and_reviewed_in_one_flow(self):
        self.api.state.side_effect=lambda:state(False)
        response=self.client.get(self.url+'?edit=1')
        self.assertContains(response,'교육자료')
        self.assertContains(response,'저장할 내용 확인')
        self.assertContains(response,'value="configure_groups"')
        import re
        editable=re.search(r'<select[^>]+aria-label="Workhub_기술팀 권한"[^>]*>',response.content.decode()).group()
        self.assertNotIn('disabled',editable)
        nonce=self.preview(action='configure_groups',group_2='write')
        self.api.change.assert_not_called()
        self.client.post(self.url+'apply/',{'nonce':nonce,'confirmed':'yes','reason':'팀 폴더 설정'})
        self.assertEqual(self.api.change.call_args.args[1],'configure_groups')
        self.assertFalse(self.api.change.call_args.args[0]['unique'])

    def test_combined_review_explains_scope_and_noop_is_read_only(self):
        self.api.state.side_effect=lambda:state(False)
        response=self.client.post(self.url+'review/',{'action':'configure_groups','group_2':'write'})
        self.assertContains(response,'교육자료부터 별도로 적용합니다.')
        self.assertContains(response,'이미 별도로 설정된 하위 폴더는 유지합니다.')
        response=self.client.post(self.url+'review/',{'action':'configure_groups','group_2':'read'})
        self.assertEqual(response.status_code,302)
        self.api.change.assert_not_called()
    def test_employee_denied_all_admin_endpoints(self):
        self.client.force_login(self.worker)
        for method,url in [('get',self.url),('post',self.url+'review/'),('post',self.url+'apply/'),
                           ('get','/work/shared-documents/permissions/workspace/'),('get','/work/shared-documents/permissions/tree/')]:
            self.assertEqual(getattr(self.client,method)(url).status_code,403)
        self.factory.assert_not_called()
    def test_unmanaged_group_cannot_modify_members(self):
        DocumentAccessGroup.objects.all().delete()
        response=self.client.post(self.url+'review/',{'action':'members','group':'2','add':[str(self.staff.pk)]})
        self.assertEqual(response.status_code,302); self.api.group_change.assert_not_called()
    def test_register_external_group_then_manage_members(self):
        DocumentAccessGroup.objects.all().delete()
        self.api.groups.return_value=[{'id':12,'name':'팀 · 보안기술팀','login':''}]
        response=self.client.get(self.url+'?tab=groups&group=12')
        self.assertContains(response,'이 그룹의 구성원 관리 연결')
        nonce=self.preview(action='register_group',group='12')
        self.assertFalse(DocumentAccessGroup.objects.exists())
        self.client.post(self.url+'apply/',{'nonce':nonce,'confirmed':'yes','reason':'팀별 관리'})
        self.assertTrue(DocumentAccessGroup.objects.filter(principal_id=12,name='팀 · 보안기술팀').exists())
        self.api.group_change.assert_not_called(); self.api.change.assert_not_called()
        response=self.client.get(self.url+'?tab=groups&group=12')
        self.assertContains(response,'구성원 변경 검토')

    def test_register_rechecks_basic_group_protection_at_apply(self):
        DocumentAccessGroup.objects.all().delete()
        nonce=self.preview(action='register_group',group='2')
        self.api.check_membership_group.side_effect=SharePointProbeError('기본 그룹 보호')
        self.client.post(self.url+'apply/',{'nonce':nonce,'confirmed':'yes','reason':'관리 연결'})
        self.assertFalse(DocumentAccessGroup.objects.exists())

    def test_external_group_rename_after_preview_blocks_registration(self):
        DocumentAccessGroup.objects.all().delete()
        nonce=self.preview(action='register_group',group='2')
        self.api.groups.return_value=[{'id':2,'name':'다른 그룹','login':''}]
        self.client.post(self.url+'apply/',{'nonce':nonce,'confirmed':'yes','reason':'관리 연결'})
        self.assertFalse(DocumentAccessGroup.objects.exists())
    def test_staff_disabled_after_review(self):
        nonce=self.preview(action='members',group='2',add=[str(self.staff.pk)])
        self.staff.is_active=False; self.staff.save()
        self.client.post(self.url+'apply/',{'nonce':nonce,'confirmed':'yes','reason':'변경'}); self.api.group_change.assert_not_called()
    def test_site_wide_lock_blocks_overlapping_folder_group_writes(self):
        DocumentPermissionLease.objects.create(target=digest(['document-permission-site',self.api.config.site_url]),expires_at=timezone.now()+timedelta(minutes=1))
        self.client.post(self.url+'apply/',{'nonce':self.preview(),'confirmed':'yes','reason':'변경'}); self.api.change.assert_not_called()
    def test_partial_failure_logged(self):
        self.api.change.side_effect=SharePointProbeError('중단')
        self.client.post(self.url+'apply/',{'nonce':self.preview(),'confirmed':'yes','reason':'변경'})
        self.assertTrue(SystemAudit.objects.filter(action='permission_attention').exists()); self.assertFalse(DocumentPermissionLease.objects.exists())
    def test_csrf(self):
        strict=Client(enforce_csrf_checks=True); strict.force_login(self.admin)
        self.assertEqual(strict.post(self.url+'review/',{}).status_code,403)
    def test_legacy_individual_replace_not_accepted(self):
        self.assertEqual(self.client.post(self.url,{'employee_1':'write'}).status_code,405); self.api.change.assert_not_called()
    def test_tree_is_bounded_and_only_returns_folders(self):
        self.api.docs.children_page.return_value=([{'id':'a','name':'폴더','folder':{}},{'id':'b','name':'파일','file':{}}],'cursor',50)
        response=self.client.get('/work/shared-documents/permissions/tree/?drive=drive&folder=root')
        self.assertEqual(response.json(),{'rows':[{'id':'a','name':'폴더'}],'cursor':'cursor'})
    def test_oauth_state_mismatch(self):
        with patch('tasks.document_permissions.ProbeConfig.from_settings'),patch('tasks.document_permissions.token_request') as token:
            self.client.get('/work/shared-documents/permissions/callback/?state=wrong&code=secret')
        token.assert_not_called()
