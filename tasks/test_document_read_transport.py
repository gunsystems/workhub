from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest.mock import patch
from urllib.error import HTTPError, URLError

import requests
from django.test import SimpleTestCase

from .document_read_transport import read_graph, session


class Response:
    def __init__(self, status=200, chunks=(b'{}',)):
        self.status_code, self.chunks, self.closed = status, chunks, False

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.closed = True

    def iter_content(self, chunk_size):
        return iter(self.chunks)


class DocumentReadTransportTests(SimpleTestCase):
    def test_connection_pool_is_reused_per_thread_not_between_threads(self):
        self.assertIs(session(), session())
        barrier = Barrier(2)
        def worker():
            client = session()
            barrier.wait(timeout=5)
            return client
        with ThreadPoolExecutor(max_workers=2) as executor:
            first, second = list(executor.map(lambda _: worker(), range(2)))
        self.assertIsNot(first, second)
        first.close()
        second.close()

    def test_only_current_bearer_is_sent_not_prior_identity_cookies_or_auth(self):
        client = requests.Session()
        self.addCleanup(client.close)
        client.headers.update(Authorization='Bearer previous-user', Cookie='private=previous')
        client.auth = ('previous', 'secret')
        client.cookies.set('private', 'previous')
        response = Response()
        with patch('tasks.document_read_transport.session', return_value=client), patch.object(client, 'send', return_value=response) as send:
            for token in ('first-user', 'second-user'):
                self.assertEqual(read_graph('https://graph.microsoft.com/v1.0/sites/site/drives', token=token), b'{}')
                prepared = send.call_args.args[0]
                self.assertEqual(prepared.headers['Authorization'], 'Bearer ' + token)
                self.assertNotIn('Cookie', prepared.headers)
                self.assertFalse(send.call_args.kwargs['allow_redirects'])
                self.assertTrue(send.call_args.kwargs['verify'])
                self.assertTrue(send.call_args.kwargs['stream'])
            read_graph('https://graph.microsoft.com/v1.0/sites/site/drives')
            self.assertNotIn('Authorization', send.call_args.args[0].headers)
        self.assertTrue(response.closed)

    def test_redirect_denial_throttle_and_timeout_do_not_retry_or_follow(self):
        client = requests.Session()
        self.addCleanup(client.close)
        for status in (301, 302, 307, 401, 403, 404, 429, 500):
            response = Response(status=status)
            with patch('tasks.document_read_transport.session', return_value=client), patch.object(client, 'send', return_value=response) as send:
                with self.assertRaises(HTTPError):
                    read_graph('https://graph.microsoft.com/v1.0/sites/site/drives', token='user')
                send.assert_called_once()
                self.assertTrue(response.closed)
        with patch('tasks.document_read_transport.session', return_value=client), patch.object(client, 'send', side_effect=requests.Timeout('private-token')):
            with self.assertRaises(URLError) as caught:
                read_graph('https://graph.microsoft.com/v1.0/sites/site/drives')
            self.assertNotIn('private-token', str(caught.exception))

    def test_large_decoded_response_and_non_read_batch_are_blocked(self):
        client = requests.Session()
        self.addCleanup(client.close)
        response = Response(chunks=(b'a' * 600000, b'b' * 600000))
        with patch('tasks.document_read_transport.session', return_value=client), patch.object(client, 'send', return_value=response) as send:
            with self.assertRaises(ValueError):
                read_graph('https://graph.microsoft.com/v1.0/sites/site/drives')
            self.assertTrue(response.closed)
        with patch('tasks.document_read_transport.session') as factory:
            for url in ('https://evil.test/v1.0/sites/site', 'http://graph.microsoft.com/v1.0/sites/site',
                        'https://graph.microsoft.com:443/v1.0/sites/site', 'https://graph.microsoft.com/v1.0/sites/site#fragment'):
                with self.assertRaises(ValueError):
                    read_graph(url)
            for method in ('POST', 'DELETE', 'PATCH'):
                with self.assertRaises(ValueError):
                    read_graph('https://graph.microsoft.com/v1.0/$batch', json_data={'requests': [
                        {'id': '1', 'method': method, 'url': '/drives/drive/items/item'}]})
            with self.assertRaises(ValueError):
                read_graph('https://graph.microsoft.com/v1.0/$batch', json_data={'requests': [
                    {'id': '1', 'method': 'GET', 'url': '/drives/drive/items/item', 'headers': {'Authorization': 'other'}}]})
            factory.assert_not_called()
