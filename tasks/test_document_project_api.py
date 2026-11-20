from types import SimpleNamespace
from unittest.mock import patch
from django.test import SimpleTestCase
from .document_project_api import ProjectAPI
from .sharepoint_probe import SharePointProbeError


class ProjectTransportTests(SimpleTestCase):
    def setUp(self):
        self.api=object.__new__(ProjectAPI)
        self.api.config=SimpleNamespace(site_url='https://example.sharepoint.com/sites/workhub')

    def test_move_preserves_history_and_never_replaces_or_bypasses_locks(self):
        response={'value':[{'JobId':'id','JobQueueUri':'https://queue.example/?secret=test','EncryptionKey':'test-key'}]}
        with patch.object(self.api,'site_call',return_value=response) as call:
            encrypted=self.api.start_move(self.api.config.site_url+'/Projects/project',self.api.config.site_url+'/Documents/2026','etag')
        options=call.call_args.args[1]['options']
        self.assertTrue(options['IsMoveMode']);self.assertFalse(options['IgnoreVersionHistory'])
        for field in ('MoveButKeepSource','AllowSchemaMismatch','AllowSmallerVersionLimitOnDestination','BypassSharedLock'):
            self.assertFalse(options[field])
        self.assertEqual(options['NameConflictBehavior'],0)
        self.assertNotIn('test-key',encrypted);self.assertNotIn('queue.example',encrypted)
        with patch.object(self.api,'site_call',return_value={'JobState':0,'Logs':[]}) as call:
            self.assertTrue(self.api.move_done(encrypted))
        self.assertEqual(call.call_args.args[0],'GetCopyJobProgress')
        with patch.object(self.api,'site_call',return_value={'JobState':0,'Logs':['{"Event":"JobEnd","ErrorCode":"0"}']}):
            self.assertTrue(self.api.move_done(encrypted))
        with patch.object(self.api,'site_call',return_value={'JobState':0,'Logs':['{"Event":"JobError","ErrorCode":-123}']}):
            with self.assertRaisesRegex(SharePointProbeError,'-123'):self.api.move_done(encrypted)

    def test_cross_site_and_unknown_jobs_are_rejected(self):
        with patch.object(self.api,'site_call') as call:
            with self.assertRaises(SharePointProbeError):self.api.start_move('https://evil.test/folder',self.api.config.site_url+'/x','e')
            call.assert_not_called()
        with patch.object(self.api,'site_call',return_value={'value':[]}):
            with self.assertRaises(SharePointProbeError):self.api.start_move(self.api.config.site_url+'/x',self.api.config.site_url+'/y','e')
