from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import patch, Mock
from django.contrib.auth import get_user_model
from django.test import TestCase, Client
from django.utils import timezone
from datetime import timedelta
from .models import Department, Employee, SystemAudit, DocumentPermissionLease
from .document_permission_api import PermissionClient, digest
from .sharepoint_probe import SharePointProbeError


class PermissionConnectionTests(TestCase):
    def test_connection_is_not_available_to_employee_even_if_site_owner(self):
        user = get_user_model().objects.create_user('member')
        from django.core.exceptions import PermissionDenied
        with self.assertRaises(PermissionDenied):
            PermissionClient(user)

    def test_transport_refuses_redirect_and_does_not_expose_provider_body(self):
        api = object.__new__(PermissionClient)
        api.config = SimpleNamespace(site_url='https://tenant.sharepoint.com/sites/work')
        api.token = 'secret'
        response = Mock(status_code=302)
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        with patch('tasks.document_permission_api.requests.request', return_value=response) as send:
            with self.assertRaises(SharePointProbeError): api.call('currentuser')
        self.assertFalse(send.call_args.kwargs['allow_redirects'])
        self.assertEqual(send.call_args.args[1], 'https://tenant.sharepoint.com/sites/work/_api/web/currentuser')

    def test_missing_scope_connection_does_not_fallback_to_application_token(self):
        user = get_user_model().objects.create_superuser('owner', 'owner@gunsystems.co.kr', 'unused')
        with patch('tasks.document_permission_api.ProbeConfig.from_settings', return_value=SimpleNamespace(
                tenant_id='t', client_id='c', site_url='https://tenant.sharepoint.com/sites/work')), patch(
                'tasks.document_permission_api.token_request') as token:
            with self.assertRaises(SharePointProbeError): PermissionClient(user)
            token.assert_not_called()


