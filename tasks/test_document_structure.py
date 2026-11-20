from copy import deepcopy
from django.core.exceptions import ValidationError
from django.test import SimpleTestCase, TestCase, override_settings
from .document_structure import (COMMON_ROOT_GRANTS, VENDORS, VENDOR_BASES,
    fixed_folders, project_folders, project_name, preview, validate_name, inventory_paths)


class StructurePolicyTests(SimpleTestCase):
    def test_exact_fixed_tree_and_only_designated_boundaries(self):
        folders = fixed_folders()
        self.assertEqual(len(folders), 51)
        self.assertEqual(len({f.display for f in folders}), 51)
        self.assertEqual({f.path for f in folders if f.unique}, {
            ('01_영업자료',), ('01_영업자료','03_제안서'), ('02_기술자료',),
            ('02_기술자료','02_벤더_제품_기술자료'), ('02_기술자료','03_교육_인증')})
        self.assertTrue(all(len(f.path) <= 3 for f in folders))
        self.assertEqual(dict(COMMON_ROOT_GRANTS), {'admins':'manage','staff':'read'})

    def test_vendor_added_in_exactly_six_places_without_unique_permissions(self):
        old = {f.display for f in fixed_folders()}
        added = [f for f in fixed_folders(VENDORS + ('NewVendor',)) if f.display not in old]
        self.assertEqual(len(added), 6)
        self.assertEqual({f.path[:-1] for f in added}, set(VENDOR_BASES))
        self.assertTrue(all(not f.unique for f in added))
        with self.assertRaises(ValidationError): fixed_folders(('Tenable','tenable'))

    def test_permission_matrix_no_staff_or_participant_leak_into_documents(self):
        grants = {f.path: dict(f.grants) for f in fixed_folders() if f.unique}
        self.assertEqual(grants[('01_영업자료',)]['support'], 'write_no_delete')
        self.assertNotIn('technical', grants[('01_영업자료',)])
        self.assertEqual(grants[('01_영업자료','03_제안서')]['technical'], 'read')
        self.assertNotIn('sales', grants[('02_기술자료',)])
        self.assertEqual(grants[('02_기술자료',)]['technical'], 'write_no_delete')
        self.assertEqual(grants[('02_기술자료','03_교육_인증')]['technical'], 'write')
        self.assertTrue(all('staff' not in r and 'participants' not in r for r in grants.values()))

    def test_complete_project_has_approved_extra_root_boundary(self):
        name = project_name(2026,customer='A사',vendor='Tenable',kind='PoC')
        active = project_folders(name)
        self.assertEqual(dict(active[0].grants)['participants'], 'write')
        self.assertTrue(all(not f.unique for f in active[1:]))
        archived = project_folders(name, completed=True, year=2027)
        self.assertEqual(archived[0].path, ('03_완료프로젝트','2027',name))
        self.assertEqual([f.unique for f in archived], [True,True,True,False,False])
        self.assertTrue(all(len(f.path) <= 4 for f in archived))
        self.assertTrue(all('participants' not in dict(f.grants) for f in archived))
        self.assertEqual(dict(archived[1].grants)['support'], 'read')
        self.assertNotIn('technical',dict(archived[1].grants))

    def test_names_enforce_internal_external_inputs_and_limits(self):
        self.assertEqual(project_name(2026,internal='홈페이지개편'), '2026_내부_홈페이지개편')
        for value in ('bad name',' bad','bad.','CON.txt','a/b','a#b','a%b','~$lock','a\nb'):
            with self.subTest(value=value), self.assertRaises(ValidationError): validate_name(value)
        for values in (dict(customer='내부',vendor='Tenable',kind='구축'),
                       dict(customer='A',vendor='직접입력',kind='구축'),
                       dict(customer='A'*40,vendor='Tenable',kind='구축'),
                       dict(internal='과제',customer='고객')):
            with self.subTest(values=values), self.assertRaises(ValidationError): project_name(2026,**values)

    def test_preview_marks_existing_for_review_without_mutating_or_adopting_it(self):
        existing = [('common', ('01_회사소개_ci',)), ('documents',('01_영업자료',))]
        before=deepcopy(existing)
        first=preview(existing);second=preview(existing)
        self.assertEqual(first,second)
        self.assertEqual(existing,before)
        self.assertEqual(sum(r['action']=='existing_review' for r in first),2)
        self.assertTrue(all(r['action'] in ('create','existing_review') for r in first))

    def test_legacy_common_is_not_adopted_and_invalid_ancestry_fails(self):
        payload={'libraries':[{'id':'old','name':'전사 공통'},{'id':'new','name':'전사공통'}], 'nodes':[
            {'key':'old/root','parent':None,'drive':'old','item':'root','name':'전사 공통'},
            {'key':'old/a','parent':'old/root','drive':'old','item':'a','name':'01_회사소개_CI'},
            {'key':'new/root','parent':None,'drive':'new','item':'root','name':'전사공통'},
            {'key':'new/a','parent':'new/root','drive':'new','item':'a','name':'03_양식_템플릿'}]}
        self.assertEqual(inventory_paths(payload), [('common',('03_양식_템플릿',))])
        payload['nodes'][-1]['parent']='old/root'
        with self.assertRaises(ValidationError): inventory_paths(payload)


@override_settings(WORKHUB_REQUIRE_HIWORKS=False, WORKHUB_SCHEDULER_ENABLED=False)
class StructurePreviewTests(TestCase):
    def setUp(self):
        from django.contrib.auth import get_user_model
        from .models import PermissionReportSnapshot
        from django.utils import timezone
        self.user=get_user_model().objects.create_superuser('structure-admin','admin@example.test','unused')
        self.client.force_login(self.user)
        self.row=PermissionReportSnapshot.objects.create(user=self.user,scope_key='safe',
            site_url='https://example.sharepoint.com/sites/documents',completed_at=timezone.now(),
            payload={'libraries':[{'id':'old','name':'전사 공통'},{'id':'docs','name':'문서'}], 'nodes':[
                {'key':'old/root','parent':None,'drive':'old','item':'root','name':'전사 공통'},
                {'key':'docs/root','parent':None,'drive':'docs','item':'root','name':'문서'},
                {'key':'docs/legacy','parent':'docs/root','drive':'docs','item':'legacy','name':'업무 참고자료'}]})

    def page(self):
        from unittest.mock import patch
        with patch('tasks.document_structure_views.scope',return_value=('safe',self.row.site_url)):
            return self.client.get('/work/shared-documents/structure/plan/')

    def test_admin_sees_plan_no_remote_client_or_mutation(self):
        from unittest.mock import patch
        from .models import SystemAudit
        before=(self.row.payload,SystemAudit.objects.count())
        with patch('tasks.group_permission_api.GroupPermissionClient.__init__',side_effect=AssertionError('remote client forbidden')):
            response=self.page()
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.context['create_count'],51)
        self.assertEqual(response.context['existing_count'],0)
        self.assertContains(response,'편집(삭제불가)')
        self.assertIn('no-store',response.headers['Cache-Control'])
        self.row.refresh_from_db()
        self.assertEqual(before,(self.row.payload,SystemAudit.objects.count()))

    def test_changed_connection_does_not_disclose_old_plan(self):
        from unittest.mock import patch
        with patch('tasks.document_structure_views.scope',return_value=('different',self.row.site_url)):
            response=self.client.get('/work/shared-documents/structure/plan/')
        self.assertContains(response,'먼저 동기화')
        self.assertNotContains(response,'생성 예정 51개')

    def test_post_not_supported_and_employee_denied(self):
        self.assertEqual(self.client.post('/work/shared-documents/structure/plan/').status_code,405)
        from django.contrib.auth import get_user_model
        from accounts.models import AccessProfile
        from django.utils import timezone
        user=get_user_model().objects.create_user('structure-staff',email='staff@example.test')
        AccessProfile.objects.create(user=user,email=user.email,verified_at=timezone.now(),require_mfa=False)
        self.client.force_login(user)
        self.assertEqual(self.client.get('/work/shared-documents/structure/plan/').status_code,403)
