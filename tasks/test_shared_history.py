from copy import deepcopy
import json
from types import SimpleNamespace
from unittest.mock import Mock, patch
from django.contrib.auth import get_user_model
from django.http import Http404
from django.test import TestCase
from .shared_history import version_page
from .shared_documents import cipher, GRAPH
from .sharepoint_probe import SharePointProbeError


class VersionHistoryTests(TestCase):
    def setUp(self):
        self.file={'id':'file','name':'제안서.docx','file':{},'parentReference':{'driveId':'drive','id':'parent'}}
        self.docs=SimpleNamespace(item=Mock(return_value=self.file),token='delegated',cursor_identity=('DocumentConnection',1,'subject'))
        self.resource=GRAPH+'/drives/drive/items/file/versions'
        self.version={'id':'2.0','lastModifiedDateTime':'2026-11-17T00:30:00Z','lastModifiedBy':{'user':{'displayName':'직원'}},'size':120}
    def test_delegated_bounded_metadata_only(self):
        raw=deepcopy(self.version); raw['@microsoft.graph.downloadUrl']='https://secret.example/token'
        with patch('tasks.shared_history._json_request',return_value={'value':[raw]}) as get:
            target,rows,cursor=version_page(self.docs,'drive','file')
        get.assert_called_once_with(self.resource+'?$top=50',token='delegated')
        self.assertEqual(rows[0]['actor'],'직원'); self.assertEqual(rows[0]['version'],'2.0')
        self.assertEqual(rows[0]['at'].utcoffset().total_seconds(),0)
        self.assertNotIn('downloadUrl',repr(rows));self.assertEqual(cursor,'')
    def test_cursor_scoped_to_user_file_and_fresh_access(self):
        with patch('tasks.shared_history._json_request',return_value={'value':[self.version],'@odata.nextLink':self.resource+'?$skiptoken=next'}) as get:
            _,_,cursor=version_page(self.docs,'drive','file')
            self.docs.cursor_identity=('DocumentConnection',2,'subject')
            with self.assertRaises(SharePointProbeError):version_page(self.docs,'drive','file',cursor)
        self.assertEqual(get.call_count,1);self.assertEqual(self.docs.item.call_count,2)
    def test_expired_tampered_and_external_cursor_fail_before_request(self):
        import time
        scope=[list(self.docs.cursor_identity),'drive','file','versions']
        bad=[cipher().encrypt(json.dumps({'scope':scope,'url':'https://evil.test/versions'}).encode()).decode(),
             cipher().encrypt_at_time(json.dumps({'scope':scope,'url':self.resource}).encode(),int(time.time())-901).decode(),'tampered']
        with patch('tasks.shared_history._json_request') as get:
            for cursor in bad:
                with self.assertRaises(SharePointProbeError):version_page(self.docs,'drive','file',cursor)
        get.assert_not_called()
    def test_external_nextlink_is_rejected_without_following(self):
        with patch('tasks.shared_history._json_request',return_value={'value':[],'@odata.nextLink':'https://evil.test/'}) as get:
            with self.assertRaises(SharePointProbeError):version_page(self.docs,'drive','file')
        self.assertEqual(get.call_count,1)
    def test_access_revocation_and_folder_cannot_reveal_history(self):
        with patch('tasks.shared_history._json_request') as get:
            self.docs.item.side_effect=SharePointProbeError('접근 불가')
            with self.assertRaises(SharePointProbeError):version_page(self.docs,'drive','file')
            self.docs.item.side_effect=None;self.docs.item.return_value={'folder':{}}
            with self.assertRaises(Http404):version_page(self.docs,'drive','file')
        get.assert_not_called()
    def test_missing_actor_time_and_bad_sizes_not_fabricated(self):
        with patch('tasks.shared_history._json_request',return_value={'value':[{'id':'1.0','size':-1}]}):
            _,rows,_=version_page(self.docs,'drive','file')
        self.assertEqual(rows[0]['actor'],'수정자 정보 없음');self.assertIsNone(rows[0]['at']);self.assertIsNone(rows[0]['size'])
    def test_invalid_version_and_repeating_page_fail_closed(self):
        for raw in ({'value':[{'id':'../secret'}]},{'value':[],'@odata.nextLink':self.resource+'?$top=50'}):
            with patch('tasks.shared_history._json_request',return_value=raw):
                with self.assertRaises(SharePointProbeError):version_page(self.docs,'drive','file')
    def test_view_is_private_readonly_and_explains_last_editor(self):
        user=get_user_model().objects.create_user('history',email='history@gunsystems.co.kr');self.client.force_login(user)
        with patch('tasks.shared_history.UserDocuments',return_value=self.docs),patch('tasks.shared_history._json_request',return_value={'value':[self.version]}):
            response=self.client.get('/work/shared-documents/drive/file/history/')
            self.assertContains(response,'그 버전의 마지막 수정자');self.assertContains(response,'버전 2.0')
            self.assertIn('no-store',response.headers['Cache-Control'])
            self.assertEqual(self.client.post('/work/shared-documents/drive/file/history/').status_code,405)
        self.client.logout();self.assertEqual(self.client.get('/work/shared-documents/drive/file/history/').status_code,302)
