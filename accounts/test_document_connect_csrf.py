"""HTTPS document connection and safe recovery for every employee role."""
import re
from urllib.parse import urlsplit,parse_qs
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.test import Client,TestCase,override_settings
from tasks.sharepoint_probe import SharePointProbeError
from tasks.models import DocumentConnection

@override_settings(DEBUG=False,WORKHUB_REQUIRE_HIWORKS=False,WORKHUB_SCHEDULER_ENABLED=False,
 SESSION_COOKIE_SECURE=True,CSRF_COOKIE_SECURE=True,SECURE_SSL_REDIRECT=True,
 SECURE_PROXY_SSL_HEADER=('HTTP_X_FORWARDED_PROTO','https'),
 SHAREPOINT_TENANT_ID='11111111-1111-1111-1111-111111111111',
 SHAREPOINT_CLIENT_ID='22222222-2222-2222-2222-222222222222',
 SHAREPOINT_CLIENT_SECRET='test-only',SHAREPOINT_SITE_URL='https://example.sharepoint.com/sites/work')
class DocumentConnectCSRFFlowTests(TestCase):
 proxy={'HTTP_X_FORWARDED_PROTO':'https','HTTP_HOST':'testserver'}
 path='/work/shared-documents/connect/'
 landing='/work/shared-documents/'
 def setUp(self):
  self.users=[get_user_model().objects.create_user('document-staff-'+str(i),email='staff'+str(i)+'@gunsystems.co.kr') for i in range(3)]
 def open(self,user):
  self.client=Client(enforce_csrf_checks=True);self.client.force_login(user)
  with patch('tasks.shared_documents.UserDocuments',side_effect=SharePointProbeError('회사 문서함 연결 필요')):
   page=self.client.get(self.landing,**self.proxy)
  self.assertEqual(page.status_code,200)
  token=re.search(r'name="csrfmiddlewaretoken" value="([^"]+)"',page.content.decode()).group(1)
  return page,token
 def test_all_staff_can_start_with_same_origin_referer_without_origin(self):
  for user in self.users:
   with self.subTest(user=user.username):
    page,token=self.open(user)
    self.assertEqual(page['Referrer-Policy'],'same-origin')
    response=self.client.post(self.path,{'csrfmiddlewaretoken':token},HTTP_REFERER='https://testserver'+self.landing,**self.proxy)
    self.assertEqual(response.status_code,302)
    self.assertEqual(urlsplit(response.url).hostname,'login.microsoftonline.com')
    self.assertEqual(self.client.session['document_oauth']['user'],user.pk)
    self.assertFalse(DocumentConnection.objects.exists())
 def test_stale_form_recovers_to_get_page_then_requires_new_post(self):
  _,token=self.open(self.users[0]);self.client.cookies['csrftoken']='B'*32
  response=self.client.post(self.path,{'csrfmiddlewaretoken':token},HTTP_ORIGIN='https://testserver',**self.proxy)
  self.assertEqual(response.status_code,403)
  self.assertContains(response,'href="'+self.landing+'"',status_code=403)
  self.assertNotIn('document_oauth',self.client.session)
  with patch('tasks.shared_documents.UserDocuments',side_effect=SharePointProbeError('연결 필요')):
   recovered=self.client.get(self.landing,**self.proxy)
  fresh=re.search(r'name="csrfmiddlewaretoken" value="([^"]+)"',recovered.content.decode()).group(1)
  response=self.client.post(self.path,{'csrfmiddlewaretoken':fresh},HTTP_ORIGIN='https://testserver',**self.proxy)
  self.assertEqual(response.status_code,302)
  self.assertEqual(self.client.session['document_oauth']['user'],self.users[0].pk)
 def test_foreign_origin_and_missing_cookie_do_not_start_oauth(self):
  _,token=self.open(self.users[0])
  for origin in ['https://evil.example','null']:
   self.assertEqual(self.client.post(self.path,{'csrfmiddlewaretoken':token},HTTP_ORIGIN=origin,**self.proxy).status_code,403)
  del self.client.cookies['csrftoken']
  self.assertEqual(self.client.post(self.path,{'csrfmiddlewaretoken':token},HTTP_ORIGIN='https://testserver',**self.proxy).status_code,403)
  self.assertNotIn('document_oauth',self.client.session)
 def test_connect_get_returns_safe_landing_without_starting_oauth(self):
  self.open(self.users[0]);response=self.client.get(self.path,**self.proxy)
  self.assertEqual(response.status_code,302);self.assertEqual(response.url,self.landing)
  self.assertNotIn('document_oauth',self.client.session)
 def test_admin_connect_failure_recovers_to_admin_setup(self):
  self.open(self.users[0])
  response=self.client.post('/work/shared-documents/permissions/connect/',{},HTTP_ORIGIN='https://testserver',**self.proxy)
  self.assertEqual(response.status_code,403)
  self.assertContains(response,'href="/work/shared-documents/permissions/"',status_code=403)
  self.assertNotIn('document_permission_oauth',self.client.session)
