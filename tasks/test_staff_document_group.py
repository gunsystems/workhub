from copy import deepcopy
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import Mock,patch
from django.contrib.auth import get_user_model
from django.test import TestCase,override_settings
from django.utils import timezone
from .models import Employee,Department,DocumentAccessGroup,DocumentStaffGroup,StaffEmployment,OffboardingCase,HiworksDirectoryCache,PermissionReportName
from .staff_document_group import seed_bindings,sync_members,roster,desired_sources
from .sharepoint_probe import SharePointProbeError


def person(pid,email):
    return {'id':pid,'email':email,'name':str(pid),'login':'i:0#.f|membership|'+email,'type':1,'site_admin':False}


class FakeApi:
    def __init__(self,people):
        self.people=deepcopy(people);self.directory={r['id']:deepcopy(r) for r in people};self.operations=[]
        self.config=SimpleNamespace(site_url='https://tenant.sharepoint.com/sites/work')
    def check_membership_group(self,gid):
        if gid in (3,4,5):raise SharePointProbeError('protected')
    def members(self,gid):return deepcopy(self.people)
    def principal(self,email):
        matches=[r['id'] for r in self.directory.values() if r['email']==email]
        if len(matches)!=1:raise SharePointProbeError('unknown company account')
        return matches[0]
    def call(self,path,method='GET',data=None):
        import re
        self.operations.append((path,method,data))
        if path=='effectivebasepermissions':return {'Low':str(1<<25)}
        if path.startswith('getuserbyid('):
            pid=int(re.search(r'\((\d+)\)',path).group(1));r=self.directory[pid]
            return {'Id':pid,'Email':r['email'],'LoginName':r['login'],'PrincipalType':r['type']}
        if 'removebyid' in path:
            pid=int(re.search(r'removebyid\((\d+)\)',path).group(1));self.people=[r for r in self.people if r['id']!=pid]
        elif method=='POST':
            r=next(r for r in self.directory.values() if r['login']==data['LoginName']);self.people.append(deepcopy(r));self.people.sort(key=lambda r:r['id'])
        return {}


@override_settings(WORKHUB_EMAIL_DOMAIN='gunsystems.co.kr')
class StaffGroupTests(TestCase):
    def setUp(self):
        self.owner=get_user_model().objects.create_superuser('operator','operator@gunsystems.co.kr','unused')
        self.department=Department.objects.create(name='기술팀')
        self.a=self.employee('first','first@gunsystems.co.kr');self.b=self.employee('second','second@gunsystems.co.kr')
        self.api=FakeApi([person(10,'first@gunsystems.co.kr'),person(20,'second@gunsystems.co.kr')])
        group=DocumentAccessGroup.objects.create(site_url=self.api.config.site_url,principal_id=19,name='전 직원')
        _,bindings=seed_bindings(self.api,19)
        self.policy=DocumentStaffGroup.objects.create(group=group,owner=self.owner,connection_key='scope',bindings=bindings,enabled=True)
    def employee(self,name,email):
        user=get_user_model().objects.create_user(name,email=email)
        return Employee.objects.create(user=user,name=name,department=self.department)
    def apply(self):
        bindings,pending=sync_members(self.api,self.policy);self.policy.bindings=bindings;self.policy.save(update_fields=['bindings']);return pending
    def test_registered_hire_added_and_no_roleassignment_or_other_group_writes(self):
        self.employee('new','new@gunsystems.co.kr');self.api.directory[30]=person(30,'new@gunsystems.co.kr')
        self.assertEqual(self.apply(),0);self.assertEqual({r['id'] for r in self.api.people},{10,20,30})
        writes=[path for path,method,_ in self.api.operations if method!='GET']
        self.assertEqual(writes,['sitegroups/getbyid(19)/users'])
    def test_suspension_removes_and_reactivation_restores(self):
        self.b.is_active=False;self.b.save();self.apply();self.assertEqual({r['id'] for r in self.api.people},{10})
        self.b.is_active=True;self.b.save();self.apply();self.assertEqual({r['id'] for r in self.api.people},{10,20})
    def test_deleted_employee_removed_using_consented_identity_link(self):
        self.b.delete();self.apply();self.assertEqual({r['id'] for r in self.api.people},{10})
    def test_last_working_day_kept_then_departure_excluded(self):
        employment=StaffEmployment.objects.create(employee=self.b,joined_on=timezone.localdate()-timedelta(days=30),ended_on=timezone.localdate())
        self.apply();self.assertEqual(len(self.api.people),2)
        employment.ended_on-=timedelta(days=1);employment.save();self.apply();self.assertEqual({r['id'] for r in self.api.people},{10})
    def test_past_offboarding_removed_and_cancellation_restored(self):
        case=OffboardingCase.objects.create(employee=self.b,departure_date=timezone.localdate()-timedelta(days=1),creator=self.owner)
        self.apply();self.assertEqual(len(self.api.people),1)
        case.state='cancelled';case.save();self.apply();self.assertEqual(len(self.api.people),2)
    def test_unresolved_new_account_pending_without_name_alias_authorization(self):
        self.employee('third','third@gunsystems.co.kr')
        self.api.directory[30]=person(30,'third@tenant.onmicrosoft.com')
        PermissionReportName.objects.create(site_url=self.api.config.site_url,principal_id=30,identity='third@tenant.onmicrosoft.com',directory_email='third@gunsystems.co.kr')
        self.assertEqual(self.apply(),1);self.assertEqual({r['id'] for r in self.api.people},{10,20})
    def test_existing_alternate_login_requires_explicit_seed_review(self):
        self.api.people[1]=person(20,'second@tenant.onmicrosoft.com');self.api.directory[20]=self.api.people[1]
        PermissionReportName.objects.create(site_url=self.api.config.site_url,principal_id=20,identity='second@tenant.onmicrosoft.com',directory_email='second@gunsystems.co.kr')
        _,bindings=seed_bindings(self.api,19);self.assertEqual(bindings['employee:'+str(self.b.pk)]['principal'],20)
        self.policy.bindings=bindings;self.b.is_active=False;self.b.save();self.apply();self.assertEqual(len(self.api.people),1)
        self.b.is_active=True;self.b.save();self.apply();self.assertEqual(len(self.api.people),2)
    def test_legacy_current_members_kept_by_exact_fresh_directory_identity(self):
        legacy=person(30,'legacy@gunsystems.co.kr');self.api.people.append(legacy);self.api.directory[30]=legacy
        cache=HiworksDirectoryCache.objects.create(key='access',rows=[{'remote_id':'hr-30','email':legacy['email'],'name':'기존 직원'}],checked_at=timezone.now())
        _,self.policy.bindings=seed_bindings(self.api,19);self.apply();self.assertEqual(len(self.api.people),3)
        cache.rows=[];cache.save();self.apply();self.assertEqual(len(self.api.people),2)
    def test_stale_legacy_directory_does_not_mass_remove(self):
        source=roster();source['fresh']=False
        bindings={'directory:known':{'email':'legacy@gunsystems.co.kr','name':'직원','principal':30,'identity':'legacy@gunsystems.co.kr'}}
        desired=desired_sources(bindings,source);self.assertTrue(desired['directory:known']['hold'])
    def test_roster_change_before_write_blocks_mutation(self):
        self.b.is_active=False;self.b.save()
        def change():self.b.is_active=True;self.b.save()
        with self.assertRaises(SharePointProbeError):sync_members(self.api,self.policy,change)
        self.assertEqual(len(self.api.people),2)
    def test_unknown_manual_member_removed_only_from_automatic_group(self):
        self.api.people.append(person(99,'outside@example.com'));self.apply();self.assertEqual(len(self.api.people),2)
        self.assertTrue(any('getbyid(19)/users/removebyid(99)' in path for path,_,_ in self.api.operations))
    def test_infrastructure_group_blocked(self):
        self.policy.group.principal_id=3
        with self.assertRaises(SharePointProbeError):sync_members(self.api,self.policy)
        self.assertEqual(self.api.operations,[])

    def test_worker_runs_only_for_approved_current_admin_connection(self):
        from .staff_document_group import synchronize_due
        with patch('tasks.staff_document_group.scope',return_value=('scope',self.api.config.site_url)),patch('tasks.group_permission_api.GroupPermissionClient',return_value=self.api):
            self.assertTrue(synchronize_due());self.assertFalse(synchronize_due())
        self.policy.refresh_from_db();self.assertIsNotNone(self.policy.last_checked_at)
        self.assertEqual(self.policy.error,'')

    def test_changed_connection_stops_worker_without_membership_writes(self):
        from .staff_document_group import synchronize_due
        with patch('tasks.staff_document_group.scope',return_value=('different',self.api.config.site_url)),patch('tasks.group_permission_api.GroupPermissionClient',return_value=self.api):
            self.assertFalse(synchronize_due())
        self.assertEqual(self.api.operations,[])
        self.policy.refresh_from_db();self.assertIn('확인 필요',self.policy.error)

    def test_site_write_lease_blocks_concurrent_automatic_change(self):
        from .staff_document_group import synchronize_due
        from .models import DocumentPermissionLease
        from .document_permission_api import digest
        DocumentPermissionLease.objects.create(target=digest(['document-permission-site',self.api.config.site_url]),expires_at=timezone.now()+timedelta(minutes=5))
        with patch('tasks.staff_document_group.scope',return_value=('scope',self.api.config.site_url)),patch('tasks.group_permission_api.GroupPermissionClient',return_value=self.api):
            self.assertFalse(synchronize_due())
        self.assertEqual(self.api.operations,[])

    def test_rename_changes_title_only_and_preserves_group_id(self):
        from .group_permission_api import GroupPermissionClient
        api=object.__new__(GroupPermissionClient);api.check_membership_group=Mock()
        api.groups=Mock(side_effect=[[{'id':19,'name':'Workhub_전 직원'}],[{'id':19,'name':'전 직원'}]])
        api.call=Mock(return_value={});api.rename_group(19,'전 직원')
        api.check_membership_group.assert_called_once_with(19)
        api.call.assert_called_once_with('sitegroups/getbyid(19)','MERGE',{'Title':'전 직원'})

    def test_merge_transport_uses_only_explicit_title_fields(self):
        from .document_permission_api import PermissionClient
        api=object.__new__(PermissionClient);api.config=self.api.config;api.token='test-only'
        response=Mock(status_code=204,content=b'');response.__enter__=Mock(return_value=response);response.__exit__=Mock(return_value=False)
        with patch('tasks.document_permission_api.requests.request',return_value=response) as request:
            api.call('sitegroups/getbyid(19)','MERGE',{'Title':'전 직원'})
        self.assertEqual(request.call_args.args[0],'POST')
        self.assertEqual(request.call_args.kwargs['headers']['X-HTTP-Method'],'MERGE')
        self.assertEqual(request.call_args.kwargs['json'],{'Title':'전 직원'})
