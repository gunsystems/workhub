import uuid
from copy import deepcopy
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import Mock,patch
from django.contrib.auth import get_user_model
from django.test import TestCase,override_settings,Client
from django.utils import timezone
from .models import DocumentPermissionConnection,PermissionReportSnapshot,PermissionReportName,HiworksDirectoryCache
from .permission_inventory import current,invalidate,build,normalize_people,synchronize_due
from .sharepoint_probe import SharePointProbeError

SITE='https://example.sharepoint.com/sites/Workhub'
TENANT='11111111-1111-1111-1111-111111111111'
APP='22222222-2222-2222-2222-222222222222'


@override_settings(SHAREPOINT_TENANT_ID=TENANT,SHAREPOINT_CLIENT_ID=APP,SHAREPOINT_CLIENT_SECRET='test-secret',SHAREPOINT_SITE_URL=SITE)
class InventoryTests(TestCase):
    def setUp(self):
        self.user=get_user_model().objects.create_superuser('inventory','admin@example.com','unused')
        self.connection=DocumentPermissionConnection.objects.create(user=self.user,tenant=TENANT,client=APP,
            subject=uuid.uuid4(),email='admin@example.com',site_url=SITE,encrypted_refresh_token='not-a-token')
        self.client.force_login(self.user)
        self.row=current(self.user)
        self.payload={'libraries':[{'id':'drive','name':'공통'}],'nodes':[{'key':'drive/root','parent':None,
            'name':'공통','grants':[{'id':3,'name':'전 직원','kind':'group','role':'보기','members':[
                {'id':8,'name':'KIM','email':'kim@example.com','login':'','kind':'person'},
                {'id':20,'name':'LEEJISOO','email':'jslee@tenant.onmicrosoft.com','login':'','kind':'person'}]}]}]}
        self.base='/work/shared-documents/permissions/report/'

    def store(self):
        self.row.payload=deepcopy(self.payload);self.row.completed_at=timezone.now();self.row.next_at=timezone.now()+timedelta(minutes=15);self.row.save()

    def test_cached_page_and_status_never_call_microsoft(self):
        self.store()
        with patch('tasks.permission_report.GroupPermissionClient') as api:
            page=self.client.get(self.base);status=self.client.get(self.base+'status/')
        api.assert_not_called();self.assertContains(page,'permission-report-initial')
        self.assertEqual(status.json()['payload']['nodes'][0]['name'],'공통')
        self.assertFalse(status.json()['pending']);self.assertIn('no-store',status.headers['Cache-Control'])

    def test_connection_change_discards_previous_payload(self):
        self.store();self.connection.connected_at=timezone.now()+timedelta(seconds=1);self.connection.save()
        row=current(self.user);self.assertEqual(row.payload,{});self.assertIsNone(row.completed_at)

    def test_disconnect_denies_snapshot_and_role_revocation_denies_endpoint(self):
        self.store();self.connection.delete()
        self.assertEqual(self.client.get(self.base+'status/').status_code,400)
        self.user.is_superuser=False;self.user.save()
        self.assertEqual(self.client.get(self.base+'status/').status_code,403)

    def test_snapshot_is_not_shared_between_administrators(self):
        self.store();other=get_user_model().objects.create_superuser('other','other@example.com','unused')
        self.client.force_login(other)
        self.assertNotContains(self.client.get(self.base),'"nodes": [{')
        self.assertEqual(self.client.get(self.base+'status/').status_code,400)

    def test_refresh_is_csrf_protected_queue_only_and_retains_payload(self):
        self.store();browser=Client(enforce_csrf_checks=True);browser.force_login(self.user)
        self.assertEqual(browser.post(self.base+'refresh/').status_code,403)
        with patch('tasks.permission_report.GroupPermissionClient') as api:
            result=self.client.post(self.base+'refresh/')
        api.assert_not_called();self.assertEqual(result.status_code,202);self.assertTrue(result.json()['pending'])
        self.row.refresh_from_db();self.assertEqual(self.row.payload,self.payload)

    def test_unchanged_status_omits_large_payload(self):
        self.store();result=self.client.get(self.base+'status/',{'known':self.row.completed_at.isoformat()}).json()
        self.assertIsNone(result['payload'])

    def test_worker_failure_retains_last_complete_data_and_retries(self):
        self.store();invalidate(SITE)
        with patch('tasks.group_permission_api.GroupPermissionClient',side_effect=SharePointProbeError('private token')):
            self.assertFalse(synchronize_due())
        self.row.refresh_from_db();self.assertEqual(self.row.payload,self.payload);self.assertIn('최신 정보 확인 필요',self.row.error)
        self.assertNotIn('private',self.row.error);self.assertEqual(self.row.lease_token,'')

    def test_worker_success_replaces_once_and_defers_next_period(self):
        with patch('tasks.group_permission_api.GroupPermissionClient'),patch('tasks.permission_inventory.build',return_value=self.payload):
            self.assertTrue(synchronize_due());self.assertFalse(synchronize_due())
        self.row.refresh_from_db();self.assertEqual(self.row.payload,self.payload);self.assertIsNotNone(self.row.completed_at)
        self.assertGreater(self.row.next_at,timezone.now()+timedelta(minutes=14))

    def test_change_during_sync_fences_out_old_result(self):
        self.store();invalidate(SITE)
        def changed(*args):invalidate(SITE);return {'nodes':[]}
        with patch('tasks.group_permission_api.GroupPermissionClient'),patch('tasks.permission_inventory.build',side_effect=changed):
            self.assertFalse(synchronize_due())
        self.row.refresh_from_db();self.assertEqual(self.row.payload,self.payload);self.assertLessEqual(self.row.next_at,timezone.now())

    def test_active_lease_deduplicates_worker(self):
        self.row.lease_until=timezone.now()+timedelta(minutes=10);self.row.save()
        with patch('tasks.group_permission_api.GroupPermissionClient') as api:
            self.assertFalse(synchronize_due());api.assert_not_called()

    def test_email_match_then_explicit_alias_no_prefix_or_name_guess(self):
        HiworksDirectoryCache.objects.create(key='access',rows=[{'email':'kim@example.com','name':'김예린'},
            {'email':'jslee@example.com','name':'이지수'}])
        payload,unmatched,_=normalize_people(self.payload,SITE)
        people=payload['nodes'][0]['grants'][0]['members'];self.assertEqual(people[0]['name'],'김예린')
        self.assertEqual(people[1]['name'],'LEEJISOO');self.assertEqual(len(unmatched),1)
        PermissionReportName.objects.create(site_url=SITE,principal_id=20,identity='jslee@tenant.onmicrosoft.com',directory_email='jslee@example.com')
        payload,unmatched,_=normalize_people(self.payload,SITE)
        self.assertEqual(payload['nodes'][0]['grants'][0]['members'][1]['name'],'이지수');self.assertEqual(unmatched,[])
        self.assertEqual(self.payload['nodes'][0]['grants'][0]['members'][0]['name'],'KIM')

    def test_name_mapping_rejects_unknown_principal_and_other_identity(self):
        self.store();HiworksDirectoryCache.objects.create(key='access',rows=[{'email':'jslee@example.com','name':'이지수'}])
        self.assertEqual(self.client.post(self.base+'names/',{'principal':999,'email':'jslee@example.com'}).status_code,400)
        self.assertEqual(self.client.post(self.base+'names/',{'principal':20,'email':'evil@example.com'}).status_code,400)
        self.assertEqual(self.client.post(self.base+'names/',{'principal':20,'email':'jslee@example.com'}).status_code,200)
        payload=deepcopy(self.payload);payload['nodes'][0]['grants'][0]['members'][1]['email']='different@tenant.onmicrosoft.com'
        value,unmatched,_=normalize_people(payload,SITE)
        self.assertEqual(value['nodes'][0]['grants'][0]['members'][1]['name'],'LEEJISOO')

    def test_builder_reads_members_once_and_preserves_folder_pagination(self):
        api=Mock();api.docs.libraries=[{'id':'drive','name':'공통'}];api.bind.return_value={'name':'폴더','folder':{}}
        api.state.return_value={'unique':True,'source':{'name':'공통'},'rows':[
            {'id':3,'name':'직원','email':'','type':8,'roles':[(1,2)],'limited':False}]}
        api.members.return_value=[{'id':8,'name':'KIM','email':'kim@example.com','type':1}]
        api.docs.children_page.side_effect=[([{'id':'child','name':'하위','folder':{}}],'next',1),([], '',1),([], '',0)]
        result=build(api);self.assertEqual(len(result['nodes']),2);api.members.assert_called_once_with(3)
        self.assertEqual(result['nodes'][1]['parent'],'drive/root')

    def test_partial_member_failure_never_yields_complete_snapshot(self):
        api=Mock();api.docs.libraries=[{'id':'drive','name':'공통'}];api.bind.return_value={'name':'폴더','folder':{}}
        api.state.return_value={'unique':True,'source':{'name':'공통'},'rows':[
            {'id':3,'name':'직원','email':'','type':8,'roles':[(1,2)],'limited':False}]}
        api.members.side_effect=SharePointProbeError('incomplete')
        with self.assertRaises(SharePointProbeError):build(api)

    def test_live_member_labels_preserve_ids_addresses_and_security_flags(self):
        from .permission_inventory import named_members
        HiworksDirectoryCache.objects.create(key='access',rows=[{'email':'jslee@example.com','name':'이지수'}])
        PermissionReportName.objects.create(site_url=SITE,principal_id=20,identity='jslee@tenant.onmicrosoft.com',directory_email='jslee@example.com')
        values=[{'id':20,'name':'LEEJISOO','email':'jslee@tenant.onmicrosoft.com','type':1,'site_admin':False,'login':'original'}]
        result=named_members(values,SITE)
        self.assertEqual(result[0],{**values[0],'name':'이지수'})
        self.assertEqual(values[0]['name'],'LEEJISOO')
