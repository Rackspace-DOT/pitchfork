import os
import sys
import unittest


PITCHFORK_DIR = os.path.join(os.path.dirname(__file__), '..', 'pitchfork')
sys.path.insert(0, PITCHFORK_DIR)

import helper
from url_safety import (
    UnsafeOutboundRequest,
    _build_api_url,
    _is_private_host,
    _is_private_ipv4,
    _is_private_ipv6,
    _validate_endpoint_hostname,
    _validate_outbound_url,
    sanitize_query_filter,
)


class ProductStub(object):
    us_api = 'https://{region}.servers.api.rackspacecloud.com/v2/{ddi}'
    uk_api = 'https://lon.servers.api.rackspacecloud.com/v2/{ddi}'


class RequestStub(object):
    def __init__(self, data):
        self.json = data


class URLSafetyTests(unittest.TestCase):
    def test_private_hosts_are_rejected(self):
        self.assertTrue(_is_private_ipv4('127.0.0.1'))
        self.assertTrue(_is_private_ipv4('10.0.0.1'))
        self.assertTrue(_is_private_ipv4('172.16.0.1'))
        self.assertTrue(_is_private_ipv4('192.168.0.1'))
        self.assertTrue(_is_private_ipv4('169.254.169.254'))
        self.assertTrue(_is_private_ipv6('::1'))
        self.assertTrue(_is_private_ipv6('::ffff:127.0.0.1'))
        self.assertTrue(_is_private_host('localhost'))
        self.assertTrue(_is_private_host('metadata.google.internal'))

    def test_public_hosts_are_allowed(self):
        self.assertFalse(_is_private_ipv4('8.8.8.8'))
        self.assertFalse(_is_private_ipv6('2001:4860:4860::8888'))
        self.assertFalse(_is_private_host('api.rackspacecloud.com'))

    def test_outbound_url_rejects_unsafe_targets(self):
        unsafe_urls = [
            'file:///etc/passwd',
            'http://127.0.0.1:8080/admin',
            'http://169.254.169.254/latest/meta-data',
            'https://user:pass@api.example.com/v1',
            'https://api.example.com/v1#fragment',
        ]

        for url in unsafe_urls:
            with self.assertRaises(UnsafeOutboundRequest):
                _validate_outbound_url(url)

    def test_authenticated_requests_require_https(self):
        with self.assertRaises(UnsafeOutboundRequest):
            _validate_outbound_url(
                'http://api.example.com/v1',
                require_https=True
            )

        self.assertEqual(
            'https://api.example.com/v1',
            _validate_outbound_url(
                'https://api.example.com/v1',
                require_https=True
            )
        )

    def test_build_api_url_requires_relative_uri(self):
        self.assertEqual(
            'https://api.example.com/v2.0/tokens',
            _build_api_url('https://api.example.com/v2.0', '/tokens')
        )

        with self.assertRaises(UnsafeOutboundRequest):
            _build_api_url('https://api.example.com', 'https://evil.test/')

        with self.assertRaises(UnsafeOutboundRequest):
            _build_api_url('https://api.example.com', '//evil.test/')

        with self.assertRaises(UnsafeOutboundRequest):
            _build_api_url('https://api.example.com', '/path#fragment')

    def test_endpoint_hostname_must_match(self):
        _validate_endpoint_hostname(
            'https://{region}.servers.api.rackspacecloud.com',
            'https://dfw.servers.api.rackspacecloud.com/v2'
        )

        with self.assertRaises(UnsafeOutboundRequest):
            _validate_endpoint_hostname(
                'https://{region}.servers.api.rackspacecloud.com',
                'https://dfw.evil.test/v2'
            )

    def test_query_filter_sanitization(self):
        self.assertEqual(
            'limit=10&offset=20',
            sanitize_query_filter('?limit=10&offset=20')
        )
        self.assertEqual(
            'limit=10fragX-Test: bad',
            sanitize_query_filter('limit=10#frag\r\nX-Test: bad')
        )


class HelperSSRFTests(unittest.TestCase):
    def test_generate_api_url_for_call_builds_expected_url(self):
        request = RequestStub({
            'data_center': 'dfw',
            'ddi': '123456',
            'api_url': '/servers/detail',
            'mock': None,
            'add_filter': '?limit=10'
        })

        self.assertEqual(
            (
                'https://dfw.servers.api.rackspacecloud.com/v2/123456'
                '/servers/detail?limit=10'
            ),
            helper.generate_api_url_for_call(ProductStub(), request)
        )

    def test_generate_api_url_for_call_rejects_absolute_api_url(self):
        request = RequestStub({
            'data_center': 'dfw',
            'ddi': '123456',
            'api_url': 'http://127.0.0.1/admin',
            'mock': None,
            'add_filter': None
        })

        with self.assertRaises(UnsafeOutboundRequest):
            helper.generate_api_url_for_call(ProductStub(), request)

    def test_generate_api_url_for_call_rejects_region_host_escape(self):
        request = RequestStub({
            'data_center': 'dfw.evil',
            'ddi': '123456',
            'api_url': '/servers/detail',
            'mock': None,
            'add_filter': None
        })

        with self.assertRaises(UnsafeOutboundRequest):
            helper.generate_api_url_for_call(ProductStub(), request)

    def test_mock_url_can_keep_placeholders_for_display(self):
        request = RequestStub({
            'data_center': None,
            'ddi': None,
            'api_url': '/servers/detail',
            'mock': True,
            'add_filter': None
        })

        self.assertEqual(
            (
                'https://{region}.servers.api.rackspacecloud.com/v2/{ddi}'
                '/servers/detail'
            ),
            helper.generate_api_url_for_call(ProductStub(), request)
        )

    def test_process_api_request_rejects_before_dispatch(self):
        called = []
        original_get = helper.requests.get

        def fake_get(*args, **kwargs):
            called.append((args, kwargs))
            raise AssertionError('request should not be dispatched')

        helper.requests.get = fake_get
        try:
            result = helper.process_api_request(
                'http://127.0.0.1/admin',
                'GET',
                None,
                {},
                html_convert=False
            )
        finally:
            helper.requests.get = original_get

        self.assertEqual([], called)
        self.assertIn('not allowed', result[2].lower())

    def test_process_api_request_disables_redirects_and_sets_timeout(self):
        calls = []
        original_get = helper.requests.get

        class ResponseStub(object):
            headers = {'content-type': 'application/json'}
            content = '{}'
            text = ''
            status_code = 200

        def fake_get(*args, **kwargs):
            calls.append((args, kwargs))
            return ResponseStub()

        helper.requests.get = fake_get
        try:
            result = helper.process_api_request(
                'https://api.example.com/v1',
                'GET',
                None,
                {},
                html_convert=False
            )
        finally:
            helper.requests.get = original_get

        self.assertEqual(200, result[3])
        self.assertEqual(False, calls[0][1]['allow_redirects'])
        self.assertEqual(helper.API_REQUEST_TIMEOUT, calls[0][1]['timeout'])


if __name__ == '__main__':
    unittest.main()
