import io
from unittest.mock import patch
from urllib.error import HTTPError

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase

from .sharepoint_probe import ProbeConfig, SharePointProbeError, check_repository, _json_request


class SharePointProbeTests(SimpleTestCase):
    def setUp(self):
        self.config = ProbeConfig('11111111-1111-1111-1111-111111111111',
            '22222222-2222-2222-2222-222222222222', 'secret-not-for-output',
            'https://example.sharepoint.com/sites/WorkhubDocuments')
        self.site = {'id': 'site-id', 'webUrl': self.config.site_url}
        self.library = {'id': 'drive-id', 'name': '전사 공통',
                        'webUrl': self.config.site_url + '/DocLib'}

    def probe(self, pages):
        with patch('tasks.sharepoint_probe._json_request', side_effect=[
                {'access_token': 'private-token'}, self.site, *pages]) as request:
            result = check_repository(self.config)
            return result, request.call_args_list

    def test_only_site_and_library_metadata_are_requested(self):
        result, calls = self.probe([{'value': [self.library]}])
        self.assertEqual(result, [self.library])
        self.assertEqual(len(calls), 3)
        self.assertNotIn('/content', str(calls))
        self.assertNotIn('/permissions', str(calls))
        self.assertNotIn('secret-not-for-output', repr(self.config))

    def test_unconfigured_does_not_use_existing_teams_credentials(self):
        with self.settings(SHAREPOINT_TENANT_ID='', MICROSOFT_CLIENT_SECRET='existing'):
            with patch('tasks.sharepoint_probe._json_request') as request:
                with self.assertRaises(SharePointProbeError):
                    check_repository()
                request.assert_not_called()

    def test_site_url_rejects_credentials_ports_queries_and_other_hosts(self):
        for url in ('http://example.sharepoint.com/sites/WorkhubDocuments',
                    'https://example.sharepoint.com.evil.test/sites/WorkhubDocuments',
                    'https://user@example.sharepoint.com/sites/WorkhubDocuments',
                    'https://example.sharepoint.com:443/sites/WorkhubDocuments',
                    self.config.site_url + '?token=secret',
                    self.config.site_url + '/../other'):
            with self.subTest(url=url), self.assertRaises(SharePointProbeError):
                ProbeConfig(self.config.tenant_id, self.config.client_id, 'secret', url)

    def test_wrong_site_response_fails_closed(self):
        self.site['webUrl'] = 'https://example.sharepoint.com/sites/Other'
        with self.assertRaises(SharePointProbeError):
            self.probe([{'value': []}])

    def test_malformed_site_and_library_urls_fail_with_safe_errors(self):
        with self.assertRaises(SharePointProbeError):
            ProbeConfig(self.config.tenant_id, self.config.client_id, 'secret', 'https://[')
        self.library['webUrl'] = 'https://['
        with self.assertRaises(SharePointProbeError):
            self.probe([{'value': [self.library]}])

    def test_invalid_provider_shapes_do_not_leak_raw_response(self):
        self.site['webUrl'] = None
        with self.assertRaises(SharePointProbeError):
            self.probe([{'value': []}])

    def test_library_outside_site_fails_closed(self):
        for url in ('https://evil.test/DocLib',
                    'https://example.sharepoint.com/sites/WorkhubDocumentsOther/DocLib'):
            self.library['webUrl'] = url
            with self.subTest(url=url), self.assertRaises(SharePointProbeError):
                self.probe([{'value': [self.library]}])

    def test_next_link_must_be_same_graph_resource(self):
        for url in ('https://evil.test/steal', 'https://graph.microsoft.com/v1.0/users',
                    'https://graph.microsoft.com/v1.0/sites/site-id/drives#fragment'):
            with self.subTest(url=url), self.assertRaises(SharePointProbeError):
                self.probe([{'value': [], '@odata.nextLink': url}])

    def test_pagination_is_followed_and_loops_are_rejected(self):
        next_url = 'https://graph.microsoft.com/v1.0/sites/site-id/drives?$skiptoken=page2'
        result, calls = self.probe([{'value': [], '@odata.nextLink': next_url},
                                   {'value': [self.library]}])
        self.assertEqual(result, [self.library])
        self.assertEqual(calls[-1].args[0], next_url)
        with self.assertRaises(SharePointProbeError):
            self.probe([{'value': [], '@odata.nextLink': next_url},
                        {'value': [], '@odata.nextLink': next_url}])

    def test_access_denied_hides_provider_payload(self):
        denied = HTTPError('https://graph.microsoft.com', 403, 'private provider detail',
                           {}, io.BytesIO(b'secret-token'))
        with patch('tasks.sharepoint_probe.build_opener') as opener:
            opener.return_value.open.side_effect = denied
            with self.assertRaises(SharePointProbeError) as caught:
                _json_request('https://graph.microsoft.com/v1.0/sites/site-id')
            self.assertNotIn('secret-token', str(caught.exception))
            self.assertNotIn('private provider detail', str(caught.exception))

    def test_command_error_is_actionable_and_has_no_secret(self):
        with self.settings(SHAREPOINT_TENANT_ID=''):
            with self.assertRaises(CommandError) as caught:
                call_command('check_sharepoint_storage', stdout=io.StringIO())
            self.assertNotIn('secret', str(caught.exception))

