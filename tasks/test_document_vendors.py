from copy import deepcopy
from unittest.mock import patch
from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from .models import DocumentVendor
from .document_rollout import prepare,advance
from .document_vendors import queue_vendor,toggle,active_names,catalog
from . import test_document_rollout as rollout_testing
SITE=rollout_testing.SITE


@override_settings(WORKHUB_REQUIRE_HIWORKS=False,WORKHUB_SCHEDULER_ENABLED=False)
class VendorTests(TestCase):
    def setUp(self):
        rollout_testing.RolloutTests.setUp(self)
        scope=patch('tasks.document_vendors.scope',return_value=('scope',SITE))
        scope.start();self.addCleanup(scope.stop)

    def initial(self):
        run=prepare(self.user,self.api)
        while run.state!='done':run=advance(self.user,run.pk,run.revision,self.api)
        return run

    def test_new_vendor_creates_six_inherited_folders_only(self):
        self.initial();old=deepcopy(self.api.rows)
        acl_count=sum(r[0] in ('acl','break') for r in self.api.writes)
        run=queue_vendor(self.user,'NewVendor',self.api)
        self.assertEqual(run.data['phase'],'vendor')
        self.assertNotIn('NewVendor',active_names(SITE))
        self.assertEqual(len(run.data['steps'])-run.cursor,6)
        while run.state!='done':run=advance(self.user,run.pk,run.revision,self.api)
        self.assertEqual(len(self.api.rows)-len(old),6)
        for key,row in old.items():self.assertEqual(self.api.rows[key],row)
        self.assertEqual(sum(r[0] in ('acl','break') for r in self.api.writes),acl_count)
        self.assertIn('NewVendor',active_names(SITE))
        self.assertTrue(DocumentVendor.objects.get(name='NewVendor').ready)

    def test_deactivate_and_reactivate_do_not_touch_sharepoint(self):
        self.initial();old=deepcopy(self.api.rows);before=len(self.api.writes)
        toggle(self.user,'CrowdStrike',False)
        self.assertNotIn('CrowdStrike',active_names(SITE))
        self.assertEqual(self.api.rows,old);self.assertEqual(len(self.api.writes),before)
        toggle(self.user,'CrowdStrike',True)
        self.assertIn('CrowdStrike',active_names(SITE))
        self.assertEqual(len(self.api.writes),before)

    def test_existing_names_case_duplicates_and_invalid_names_never_queue(self):
        run=self.initial();before=len(run.data['steps'])
        for name in ('crowdstrike','Bad Name','../bad','New#Vendor','A'*81):
            with self.subTest(name=name),self.assertRaises(ValidationError):queue_vendor(self.user,name,self.api)
        run.refresh_from_db();self.assertEqual(len(run.data['steps']),before)
        self.assertEqual(run.state,'done')

    def test_pending_run_cannot_queue_or_toggle(self):
        run=prepare(self.user,self.api)
        with self.assertRaises(ValidationError):queue_vendor(self.user,'NewVendor',self.api)
        with self.assertRaises(ValidationError):toggle(self.user,'Tenable',False)
        self.assertFalse(DocumentVendor.objects.exists())
        self.assertTrue(all(not r.ready for r in catalog(SITE)))

    def test_catalog_and_http_are_admin_only_without_get_mutations(self):
        self.initial();self.client.force_login(self.user)
        with patch('tasks.document_structure_views.scope',return_value=('scope',SITE)):
            response=self.client.get('/work/shared-documents/structure/vendors/')
        self.assertEqual(response.status_code,200)
        self.assertContains(response,'비활성화')
        self.assertFalse(DocumentVendor.objects.exists())
        self.assertEqual(self.client.get('/work/shared-documents/structure/vendors/change/').status_code,405)
        self.client.logout()
        self.assertEqual(self.client.get('/work/shared-documents/structure/vendors/').status_code,302)
