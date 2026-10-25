"""Tests for runtime/tools/seo_network.py. Resolver and connections are fakes;
no real network connection is ever opened."""
import email.message
import http.client
import importlib.util
import os
import socket
import ssl
import unittest
from pathlib import Path
from unittest import mock

spec = importlib.util.spec_from_file_location(
    'seo_network', Path(__file__).resolve().parent.parent / 'runtime/tools/seo_network.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
NetworkError = m.NetworkError
validate_url = m.validate_url
PUBLIC_A = '93.184.216.34'
PUBLIC_B = '8.8.8.8'


class FakeResponse:
    def __init__(self, status=200, headers=None, body=b''):
        self.status = status
        self.headers = email.message.Message()
        for name, value in (headers or {}).items():
            self.headers[name] = value
        self.body = body
        self.read_calls = 0

    def read(self, size=-1):
        self.read_calls += 1
        if size is None or size < 0:
            data, self.body = self.body, b''
        else:
            data, self.body = self.body[:size], self.body[size:]
        return data


class FakeConnection:
    def __init__(self, response):
        self.response = response
        self.closed = False
        self.calls = []

    def request(self, method, target, body=None, headers=None):
        self.calls.append({'method': method, 'target': target, 'body': body,
                           'headers': dict(headers or {})})
        if isinstance(self.response, Exception):
            raise self.response

    def getresponse(self):
        return self.response

    def close(self):
        self.closed = True


class Harness:
    """Injects a fake resolver and fake connections into a SafeFetcher."""

    def __init__(self, fetcher, resolver, connections):
        self.fetcher = fetcher
        self.resolver = dict(resolver)
        self.resolver_calls = []
        self.connection_calls = []
        self.connections = list(connections)
        fetcher._resolve_host = self._record_resolver
        fetcher._open_connection = self._record_connection

    def _record_resolver(self, hostname):
        self.resolver_calls.append(hostname)
        if hostname not in self.resolver:
            raise OSError(f'unexpected lookup {hostname}')
        return self.resolver[hostname]

    def _record_connection(self, address, port, hostname, https, timeout):
        self.connection_calls.append((address, port, hostname, https, timeout))
        if not self.connections:
            raise AssertionError('unexpected extra connection')
        return self.connections.pop(0)


def response_connection(response):
    return FakeConnection(response)


class ValidateUrlTests(unittest.TestCase):
    def test_accepts_and_normalizes(self):
        cases = [
            ('https://example.com', 'https://example.com/'),
            ('https://Example.COM:443/a?b=1', 'https://example.com/a?b=1'),
            ('http://example.com:80/x', 'http://example.com/x'),
            ('https://example.com./x', 'https://example.com/x'),
            ('https://m\xfcnchen.de/pfad', 'https://xn--mnchen-3ya.de/pfad'),
            ('https://[2001:4860:4860::8888]/dns', 'https://[2001:4860:4860::8888]/dns'),
            ('https://[2606:4700:4700::1111]/', 'https://[2606:4700:4700::1111]/'),
        ]
        for raw, expected in cases:
            with self.subTest(raw=raw):
                self.assertEqual(validate_url(raw), expected)

    def test_rejects(self):
        cases = [
            ('ftp://example.com/', 'scheme_not_allowed'),
            ('//example.com/', 'scheme_not_allowed'),
            ('example.com', 'scheme_not_allowed'),
            ('https://example.com:8080/', 'port_not_allowed'),
            ('http://example.com:443/', 'port_not_allowed'),
            ('https://user@example.com/', 'userinfo_not_allowed'),
            ('https://user:pass@example.com/', 'userinfo_not_allowed'),
            ('https://example.com\\@evil.com/', 'userinfo_not_allowed'),
            ('https://example.com/#frag', 'fragment_not_allowed'),
            ('https://exa\tmple.com/', 'invalid_character'),
            ('https://example.com/\x00', 'invalid_character'),
            ('https://exa mple.com/', 'invalid_character'),
            ('https://localhost/', 'forbidden_host'),
            ('https://api.localhost/', 'forbidden_host'),
            ('https://printer.local/', 'forbidden_host'),
            ('https://svc.internal/', 'forbidden_host'),
            ('https://ip6-localhost/', 'forbidden_host'),
            ('https://127.0.0.1/', 'non_global_ip'),
            ('https://10.1.2.3/', 'non_global_ip'),
            ('https://172.16.0.9/', 'non_global_ip'),
            ('https://192.168.1.1/', 'non_global_ip'),
            ('https://169.254.169.254/latest/meta-data/', 'non_global_ip'),
            ('https://0.0.0.0/', 'non_global_ip'),
            ('https://100.64.0.1/', 'non_global_ip'),
            ('https://224.0.0.1/', 'non_global_ip'),
            ('https://[::1]/', 'non_global_ip'),
            ('https://[::]/', 'non_global_ip'),
            ('https://[::ffff:10.0.0.1]/', 'non_global_ip'),
            ('https://[::ffff:127.0.0.1]/', 'non_global_ip'),
            ('https://[fe80::1]/', 'non_global_ip'),
            ('https://[fc00::1]/', 'non_global_ip'),
            ('https://[ff02::1]/', 'non_global_ip'),
            ('https://[2001:db8::1]/', 'non_global_ip'),
            ('https://[2002:7f00:0001::]/', 'non_global_ip'),
            ('https://999.1.1.1/', 'invalid_host'),
            ('https://exa_mple.com/', 'invalid_host'),
            ('https://-bad.example.com/', 'invalid_host'),
            ('https://a..b.example/', 'invalid_host'),
            ('https://0x7f000001/', 'invalid_host'),
            ('https://2130706433/', 'invalid_host'),
            ('', 'invalid_url'),
            (None, 'invalid_url'),
            ('https://' + 'a' * 3000 + '.com/', 'invalid_url'),
        ]
        for raw, code in cases:
            with self.subTest(raw=raw):
                with self.assertRaises(NetworkError) as caught:
                    validate_url(raw)
                self.assertEqual(caught.exception.code, code)

    def test_error_messages_are_redacted(self):
        raw = 'https://user:sekret-token@example.com:8080/path#frag'
        with self.assertRaises(NetworkError) as caught:
            validate_url(raw)
        rendered = str(caught.exception)
        for leak in ('sekret', 'example.com', 'path', '8080'):
            self.assertNotIn(leak, rendered)

    def test_network_error_attributes(self):
        error = NetworkError('some_code', 'static message')
        self.assertEqual(error.code, 'some_code')
        self.assertEqual(error.message, 'static message')
        self.assertEqual(str(error), 'some_code: static message')


class FetchTests(unittest.TestCase):
    def make_fetcher(self, **kwargs):
        return m.SafeFetcher(**kwargs)

    def test_simple_success(self):
        fetcher = self.make_fetcher()
        connection = response_connection(FakeResponse(200, {'Content-Type': 'text/plain'}, b'ok'))
        harness = Harness(fetcher, {'example.com': [PUBLIC_A]}, [connection])
        result = fetcher.fetch('https://example.com/a?b=1')
        self.assertEqual(result['url'], 'https://example.com/a?b=1')
        self.assertEqual(result['status'], 200)
        self.assertEqual(result['text'], 'ok')
        self.assertEqual(result['content_type'], 'text/plain')
        self.assertEqual(result['headers']['content-type'], 'text/plain')
        self.assertEqual(harness.resolver_calls, ['example.com'])
        self.assertEqual(harness.connection_calls, [(PUBLIC_A, 443, 'example.com', True, 8.0)])
        self.assertTrue(connection.closed)
        self.assertEqual(connection.calls[0]['method'], 'GET')
        self.assertEqual(connection.calls[0]['target'], '/a?b=1')
        self.assertNotIn('host', connection.calls[0]['headers'])

    def test_call_alias_matches_fetch(self):
        fetcher = self.make_fetcher()
        Harness(fetcher, {'example.com': [PUBLIC_A]}, [
            response_connection(FakeResponse(200, {'Content-Type': 'text/plain'}, b'ok')),
            response_connection(FakeResponse(200, {'Content-Type': 'text/plain'}, b'ok')),
        ])
        self.assertEqual(fetcher('https://example.com/'), fetcher.fetch('https://example.com/'))

    def test_pins_first_address_and_never_resolves_twice(self):
        fetcher = self.make_fetcher()
        lookups = [[PUBLIC_A], ['127.0.0.1']]
        fetcher._resolve_host = lambda hostname: lookups.pop(0)
        connection = response_connection(FakeResponse(200, {'Content-Type': 'text/plain'}, b'ok'))
        calls = []
        fetcher._open_connection = lambda address, port, hostname, https, timeout: calls.append(
            (address, port, hostname, https, timeout)) or connection
        result = fetcher.fetch('https://example.com/')
        self.assertEqual(result['status'], 200)
        self.assertEqual(calls, [(PUBLIC_A, 443, 'example.com', True, 8.0)])
        self.assertEqual(len(lookups), 1, 'resolver must be called exactly once per request')

    def test_mixed_public_and_private_addresses_rejected(self):
        fetcher = self.make_fetcher()
        connection = response_connection(FakeResponse(200, {}, b'ok'))
        harness = Harness(fetcher, {'example.com': [PUBLIC_A, '10.0.0.5']}, [connection])
        with self.assertRaises(NetworkError) as caught:
            fetcher.fetch('https://example.com/')
        self.assertEqual(caught.exception.code, 'non_global_ip')
        self.assertEqual(harness.connection_calls, [])

    def test_dns_failure_is_redacted(self):
        fetcher = self.make_fetcher()
        connection = response_connection(FakeResponse(200, {}, b''))
        fetcher._resolve_host = lambda hostname: (_ for _ in ()).throw(
            socket.gaierror('Nameserver leak ns1.leak.example token=abc'))
        fetcher._open_connection = lambda *args: connection
        with self.assertRaises(NetworkError) as caught:
            fetcher.fetch('https://example.com/')
        self.assertEqual(caught.exception.code, 'dns_failure')
        self.assertNotIn('leak', str(caught.exception))
        self.assertNotIn('abc', str(caught.exception))

    def test_multi_hop_redirect_revalidates_and_pins_each_hop(self):
        fetcher = self.make_fetcher()
        first = response_connection(FakeResponse(302, {'Location': 'https://second.example/final'}, b''))
        second = response_connection(FakeResponse(200, {'Content-Type': 'text/plain'}, b'done'))
        harness = Harness(fetcher, {
            'first.example': [PUBLIC_A],
            'second.example': [PUBLIC_B],
        }, [first, second])
        result = fetcher.fetch('https://first.example/start')
        self.assertEqual(result['url'], 'https://second.example/final')
        self.assertEqual(result['status'], 200)
        self.assertEqual(result['text'], 'done')
        self.assertEqual(harness.resolver_calls, ['first.example', 'second.example'])
        self.assertEqual(harness.connection_calls[0][:4], (PUBLIC_A, 443, 'first.example', True))
        self.assertEqual(harness.connection_calls[1][:4], (PUBLIC_B, 443, 'second.example', True))
        self.assertEqual(first.calls[0]['target'], '/start')
        self.assertEqual(second.calls[0]['target'], '/final')

    def test_redirect_downgrades_https_to_http_with_revalidation(self):
        fetcher = self.make_fetcher()
        first = response_connection(FakeResponse(302, {'Location': 'http://second.example/x?y=1'}, b''))
        second = response_connection(FakeResponse(200, {}, b''))
        harness = Harness(fetcher, {
            'first.example': [PUBLIC_A],
            'second.example': [PUBLIC_B],
        }, [first, second])
        fetcher.fetch('https://first.example/')
        self.assertEqual(harness.connection_calls[1][:4], (PUBLIC_B, 80, 'second.example', False))
        self.assertEqual(second.calls[0]['target'], '/x?y=1')

    def test_redirect_to_private_ip_rejected(self):
        fetcher = self.make_fetcher()
        first = response_connection(FakeResponse(302, {'Location': 'https://192.168.0.5/steal'}, b''))
        harness = Harness(fetcher, {'first.example': [PUBLIC_A]}, [first])
        with self.assertRaises(NetworkError) as caught:
            fetcher.fetch('https://first.example/')
        self.assertEqual(caught.exception.code, 'non_global_ip')
        self.assertEqual(len(harness.connection_calls), 1)

    def test_redirect_to_internal_hostname_rejected(self):
        fetcher = self.make_fetcher()
        first = response_connection(FakeResponse(302, {'Location': 'http://localhost:80/admin'}, b''))
        harness = Harness(fetcher, {'first.example': [PUBLIC_A]}, [first])
        with self.assertRaises(NetworkError) as caught:
            fetcher.fetch('https://first.example/')
        self.assertEqual(caught.exception.code, 'forbidden_host')
        self.assertEqual(len(harness.connection_calls), 1)

    def test_redirect_with_fragment_rejected(self):
        fetcher = self.make_fetcher()
        first = response_connection(FakeResponse(302, {'Location': 'https://second.example/x#f'}, b''))
        harness = Harness(fetcher, {'first.example': [PUBLIC_A]}, [first])
        with self.assertRaises(NetworkError) as caught:
            fetcher.fetch('https://first.example/')
        self.assertEqual(caught.exception.code, 'fragment_not_allowed')
        self.assertEqual(len(harness.connection_calls), 1)

    def test_too_many_redirects(self):
        fetcher = self.make_fetcher()
        connections = []
        for hop in range(6):
            connections.append(response_connection(
                FakeResponse(302, {'Location': f'https://first.example/hop{hop + 2}'}, b'')))
        harness = Harness(fetcher, {'first.example': [PUBLIC_A]}, connections)
        with self.assertRaises(NetworkError) as caught:
            fetcher.fetch('https://first.example/hop1')
        self.assertEqual(caught.exception.code, 'too_many_redirects')
        self.assertEqual(len(harness.connection_calls), 5)

    def test_request_budget_exhausted(self):
        fetcher = self.make_fetcher(max_requests=2)
        connections = [
            response_connection(FakeResponse(302, {'Location': 'https://first.example/2'}, b'')),
            response_connection(FakeResponse(302, {'Location': 'https://first.example/3'}, b'')),
            response_connection(FakeResponse(200, {}, b'')),
        ]
        harness = Harness(fetcher, {'first.example': [PUBLIC_A]}, connections)
        with self.assertRaises(NetworkError) as caught:
            fetcher.fetch('https://first.example/1')
        self.assertEqual(caught.exception.code, 'request_budget_exhausted')
        self.assertEqual(len(harness.connection_calls), 2)

    def test_authenticated_redirect_rejected_and_never_forwarded(self):
        fetcher = self.make_fetcher()
        first = response_connection(FakeResponse(302, {'Location': 'https://second.example/'}, b''))
        second = response_connection(FakeResponse(200, {}, b''))
        harness = Harness(fetcher, {
            'first.example': [PUBLIC_A],
            'second.example': [PUBLIC_B],
        }, [first, second])
        with self.assertRaises(NetworkError) as caught:
            fetcher.fetch('https://first.example/', headers={'Authorization': 'Bearer adapter-token'})
        self.assertEqual(caught.exception.code, 'authenticated_redirect_rejected')
        self.assertEqual(harness.connection_calls, [(PUBLIC_A, 443, 'first.example', True, 8.0)],
                         'no second request may be issued while authenticated')
        self.assertEqual(first.calls[0]['headers'].get('Authorization'), 'Bearer adapter-token')

    def test_authenticated_same_origin_redirect_also_rejected(self):
        fetcher = self.make_fetcher()
        first = response_connection(FakeResponse(302, {'Location': '/same'}, b''))
        Harness(fetcher, {'first.example': [PUBLIC_A]}, [first])
        with self.assertRaises(NetworkError) as caught:
            fetcher.fetch('https://first.example/', headers={'authorization': 'Bearer adapter-token'})
        self.assertEqual(caught.exception.code, 'authenticated_redirect_rejected')

    def test_unauthenticated_redirect_drops_no_credentials_because_none_exist(self):
        fetcher = self.make_fetcher()
        first = response_connection(FakeResponse(302, {'Location': 'https://second.example/'}, b''))
        second = response_connection(FakeResponse(200, {}, b''))
        Harness(fetcher, {'first.example': [PUBLIC_A], 'second.example': [PUBLIC_B]}, [first, second])
        fetcher.fetch('https://first.example/')
        self.assertNotIn('authorization', second.calls[0]['headers'])

    def test_post_redirect_never_replays_sensitive_body(self):
        for status in (301,302,303,307,308):
            fetcher=self.make_fetcher()
            first=response_connection(FakeResponse(status,{'Location':'https://second.example/'},b''))
            second=response_connection(FakeResponse(200,{},b''))
            harness=Harness(fetcher,{'first.example':[PUBLIC_A],'second.example':[PUBLIC_B]},[first,second])
            with self.assertRaises(NetworkError) as caught:
                fetcher.fetch('https://first.example/token',method='POST',body='client_secret=fixture')
            self.assertEqual(caught.exception.code,'post_redirect_rejected')
            self.assertEqual(len(harness.connection_calls),1)
            self.assertEqual(second.calls,[])

    def test_request_and_wall_budgets_apply_across_fetch_calls(self):
        fetcher=self.make_fetcher(max_requests=1)
        conn=response_connection(FakeResponse(200,{},b'ok'))
        harness=Harness(fetcher,{'example.com':[PUBLIC_A]},[conn])
        self.assertEqual(fetcher.fetch('https://example.com/')['body_bytes'],b'ok')
        with self.assertRaises(NetworkError) as caught:fetcher.fetch('https://example.com/second')
        self.assertEqual(caught.exception.code,'request_budget_exhausted')
        self.assertEqual(len(harness.connection_calls),1)
        clock=mock.Mock(return_value=0)
        fetcher=self.make_fetcher(clock=clock,budget_seconds=2)
        harness=Harness(fetcher,{'example.com':[PUBLIC_A]},[response_connection(FakeResponse())])
        fetcher.fetch('https://example.com/')
        clock.return_value=3
        with self.assertRaises(NetworkError) as caught:fetcher.fetch('https://example.com/other')
        self.assertEqual(caught.exception.code,'budget_exceeded')

    def test_api_key_query_is_not_forwarded_to_redirect(self):
        fetcher=self.make_fetcher()
        first=response_connection(FakeResponse(302,{'Location':'https://second.example/?key=fixture'},b''))
        second=response_connection(FakeResponse())
        harness=Harness(fetcher,{'first.example':[PUBLIC_A],'second.example':[PUBLIC_B]},[first,second])
        with self.assertRaises(NetworkError) as caught:fetcher.fetch('https://first.example/?key=fixture')
        self.assertEqual(caught.exception.code,'credential_redirect_rejected')
        self.assertEqual(second.calls,[])
    def test_content_length_over_budget_rejected_without_reading(self):
        fetcher = self.make_fetcher()
        response = FakeResponse(200, {'Content-Length': str(4 * 2097152)}, b'')
        harness = Harness(fetcher, {'example.com': [PUBLIC_A]}, [response_connection(response)])
        with self.assertRaises(NetworkError) as caught:
            fetcher.fetch('https://example.com/')
        self.assertEqual(caught.exception.code, 'response_too_large')
        self.assertEqual(response.read_calls, 0)
        self.assertTrue(harness.connections == [])

    def test_streamed_body_over_budget_rejected(self):
        fetcher = self.make_fetcher(max_bytes=16)
        response = FakeResponse(200, {}, b'x' * 17)
        harness = Harness(fetcher, {'example.com': [PUBLIC_A]}, [response_connection(response)])
        with self.assertRaises(NetworkError) as caught:
            fetcher.fetch('https://example.com/')
        self.assertEqual(caught.exception.code, 'response_too_large')

    def test_compressed_response_rejected_unread(self):
        fetcher = self.make_fetcher()
        response = FakeResponse(200, {'Content-Encoding': 'gzip', 'Content-Type': 'text/html'}, b'bomb')
        harness = Harness(fetcher, {'example.com': [PUBLIC_A]}, [response_connection(response)])
        with self.assertRaises(NetworkError) as caught:
            fetcher.fetch('https://example.com/')
        self.assertEqual(caught.exception.code, 'unsupported_compression')
        self.assertEqual(response.read_calls, 0)

    def test_identity_encoding_allowed(self):
        fetcher = self.make_fetcher()
        response = FakeResponse(200, {'Content-Encoding': 'Identity'}, b'ok')
        Harness(fetcher, {'example.com': [PUBLIC_A]}, [response_connection(response)])
        result = fetcher.fetch('https://example.com/')
        self.assertEqual(result['text'], 'ok')

    def test_request_timeout_is_redacted(self):
        fetcher = self.make_fetcher()
        connection = response_connection(socket.timeout('timed out talking to 10.0.0.1 secret=abc'))
        Harness(fetcher, {'example.com': [PUBLIC_A]}, [connection])
        with self.assertRaises(NetworkError) as caught:
            fetcher.fetch('https://example.com/')
        self.assertEqual(caught.exception.code, 'request_timeout')
        rendered = str(caught.exception)
        self.assertNotIn('10.0.0.1', rendered)
        self.assertNotIn('abc', rendered)
        self.assertEqual(len(connection.calls), 1, 'never retries')

    def test_read_timeout_is_redacted(self):
        fetcher = self.make_fetcher()
        response = FakeResponse(200, {}, b'')

        class TimeoutResponse(FakeResponse):
            def read(self, size=-1):
                raise socket.timeout('slow loris 192.168.1.1')

        connection = FakeConnection(TimeoutResponse(200, {}, b''))
        Harness(fetcher, {'example.com': [PUBLIC_A]}, [connection])
        with self.assertRaises(NetworkError) as caught:
            fetcher.fetch('https://example.com/')
        self.assertEqual(caught.exception.code, 'request_timeout')
        self.assertNotIn('192.168', str(caught.exception))

    def test_protocol_error_is_redacted(self):
        fetcher = self.make_fetcher()
        connection = response_connection(http.client.BadStatusLine('GARBAGE https://leak.example'))
        Harness(fetcher, {'example.com': [PUBLIC_A]}, [connection])
        with self.assertRaises(NetworkError) as caught:
            fetcher.fetch('https://example.com/')
        self.assertEqual(caught.exception.code, 'network_failed')
        self.assertNotIn('GARBAGE', str(caught.exception))
        self.assertNotIn('leak', str(caught.exception))

    def test_budget_exhausted_before_any_request(self):
        fetcher = self.make_fetcher(clock=mock.Mock(side_effect=[0.0, 100.0]))
        connection = response_connection(FakeResponse(200, {}, b''))
        harness = Harness(fetcher, {'example.com': [PUBLIC_A]}, [connection])
        with self.assertRaises(NetworkError) as caught:
            fetcher.fetch('https://example.com/')
        self.assertEqual(caught.exception.code, 'budget_exceeded')
        self.assertEqual(harness.connection_calls, [])

    def test_budget_exhausted_mid_read(self):
        fetcher = self.make_fetcher(clock=mock.Mock(side_effect=[0.0, 0.0, 100.0]))
        response = FakeResponse(200, {}, b'x' * 4096)
        harness = Harness(fetcher, {'example.com': [PUBLIC_A]}, [response_connection(response)])
        with self.assertRaises(NetworkError) as caught:
            fetcher.fetch('https://example.com/')
        self.assertEqual(caught.exception.code, 'budget_exceeded')
        self.assertEqual(len(harness.connection_calls), 1)

    def test_timeout_capped_by_remaining_budget(self):
        fetcher = self.make_fetcher(budget_seconds=3, timeout_seconds=8)
        connection = response_connection(FakeResponse(200, {}, b''))
        harness = Harness(fetcher, {'example.com': [PUBLIC_A]}, [connection])
        fetcher.fetch('https://example.com/')
        applied = harness.connection_calls[0][4]
        self.assertGreater(applied, 0)
        self.assertLessEqual(applied, 3.0)

    def test_default_connection_pins_ip_but_keeps_hostname_sni(self):
        fetcher = self.make_fetcher()
        raw = mock.MagicMock(name='raw_socket')
        wrapped = mock.MagicMock(name='tls_socket')
        context = mock.MagicMock(name='tls_context')
        context.wrap_socket.return_value = wrapped
        with mock.patch.object(socket, 'create_connection', return_value=raw) as create:
            with mock.patch.object(socket, 'getaddrinfo',
                                   side_effect=AssertionError('second dns lookup happened')):
                fetcher._tls_context = lambda: context
                connection = fetcher._open_connection(PUBLIC_A, 443, 'example.com', True, 5.0)
        create.assert_called_once_with((PUBLIC_A, 443), timeout=5.0)
        context.wrap_socket.assert_called_once_with(raw, server_hostname='example.com')
        self.assertIsInstance(connection, http.client.HTTPSConnection)
        self.assertEqual(connection.host, 'example.com')
        self.assertEqual(connection.port, 443)
        self.assertIs(connection.sock, wrapped)

    def test_default_connection_plain_http_uses_raw_socket(self):
        fetcher = self.make_fetcher()
        raw = mock.MagicMock(name='raw_socket')
        with mock.patch.object(socket, 'create_connection', return_value=raw) as create:
            connection = fetcher._open_connection(PUBLIC_A, 80, 'example.com', False, 2.0)
        create.assert_called_once_with((PUBLIC_A, 80), timeout=2.0)
        self.assertIsInstance(connection, http.client.HTTPConnection)
        self.assertEqual(connection.host, 'example.com')
        self.assertIs(connection.sock, raw)

    def test_tls_context_is_strict(self):
        context = self.make_fetcher()._tls_context()
        self.assertTrue(context.check_hostname)
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)

    def test_proxy_environment_is_ignored(self):
        fetcher = self.make_fetcher()
        for key in ('http_proxy', 'https_proxy', 'HTTP_PROXY', 'HTTPS_PROXY', 'all_proxy'):
            os.environ[key] = 'http://user:pass@attacker.example:3128'
            self.addCleanup(os.environ.pop, key, None)
        connection = response_connection(FakeResponse(200, {}, b''))
        harness = Harness(fetcher, {'example.com': [PUBLIC_A]}, [connection])
        fetcher.fetch('https://example.com/')
        self.assertEqual(harness.connection_calls, [(PUBLIC_A, 443, 'example.com', True, 8.0)])
        rendered = repr(harness.connection_calls) + repr(connection.calls)
        self.assertNotIn('attacker.example', rendered)
        self.assertNotIn('3128', rendered)

    def test_default_and_custom_user_agent(self):
        fetcher = self.make_fetcher()
        first = response_connection(FakeResponse(200, {}, b''))
        second = response_connection(FakeResponse(200, {}, b''))
        harness = Harness(fetcher, {'example.com': [PUBLIC_A]}, [first, second])
        fetcher.fetch('https://example.com/')
        self.assertEqual(first.calls[0]['headers']['User-Agent'], 'HarmonikaSEO')
        fetcher.fetch('https://example.com/', headers={'User-Agent': 'HarmonikaBot/1.0'})
        self.assertEqual(second.calls[0]['headers']['User-Agent'], 'HarmonikaBot/1.0')

    def test_always_requests_identity_encoding(self):
        fetcher = self.make_fetcher()
        connection = response_connection(FakeResponse(200, {}, b''))
        harness = Harness(fetcher, {'example.com': [PUBLIC_A]}, [connection])
        fetcher.fetch('https://example.com/')
        self.assertEqual(harness.connection_calls, [(PUBLIC_A, 443, 'example.com', True, 8.0)])
        self.assertEqual(connection.calls[0]['headers']['Accept-Encoding'], 'identity')

    def test_managed_headers_rejected(self):
        for name in ('Host', 'content-length', 'Accept-Encoding', 'Connection',
                     'Proxy-Authorization', 'Transfer-Encoding'):
            with self.subTest(name=name):
                fetcher = self.make_fetcher()
                Harness(fetcher, {'example.com': [PUBLIC_A]}, [response_connection(FakeResponse())])
                with self.assertRaises(NetworkError) as caught:
                    fetcher.fetch('https://example.com/', headers={name: 'evil'})
                self.assertEqual(caught.exception.code, 'invalid_header')

    def test_header_injection_rejected(self):
        fetcher = self.make_fetcher()
        Harness(fetcher, {'example.com': [PUBLIC_A]}, [response_connection(FakeResponse())])
        with self.assertRaises(NetworkError) as caught:
            fetcher.fetch('https://example.com/', headers={'X-Note': 'value\r\nX-Evil: 1'})
        self.assertEqual(caught.exception.code, 'invalid_header')

    def test_methods_and_bodies(self):
        cases = [
            ({'method': 'DELETE'}, 'method_not_allowed'),
            ({'method': 'PATCH'}, 'method_not_allowed'),
            ({'method': 'get', 'body': 'a=1'}, 'body_not_allowed'),
            ({'method': 'GET', 'body': b'bytes'}, 'body_not_allowed'),
        ]
        fetcher = self.make_fetcher()
        Harness(fetcher, {'example.com': [PUBLIC_A]}, [response_connection(FakeResponse())])
        for kwargs, code in cases:
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(NetworkError) as caught:
                    fetcher.fetch('https://example.com/', **kwargs)
                self.assertEqual(caught.exception.code, code)

    def test_post_body_encoded_and_capped(self):
        fetcher = self.make_fetcher(max_bytes=8)
        harness = Harness(fetcher, {'example.com': [PUBLIC_A]}, [])
        with self.assertRaises(NetworkError) as caught:
            fetcher.fetch('https://example.com/', method='POST', body='x' * 9)
        self.assertEqual(caught.exception.code, 'request_body_too_large')
        self.assertEqual(harness.connection_calls, [], 'oversized body must never reach the wire')
        with self.assertRaises(NetworkError) as caught:
            fetcher.fetch('https://example.com/', method='POST', body=object())
        self.assertEqual(caught.exception.code, 'invalid_body')
        fetcher2 = self.make_fetcher()
        second = response_connection(FakeResponse(200, {}, b''))
        Harness(fetcher2, {'example.com': [PUBLIC_A]}, [second])
        fetcher2.fetch('https://example.com/', method='POST', body='q=hello')
        self.assertEqual(second.calls[0]['body'], b'q=hello')

    def test_charset_decode_is_bounded_and_safe(self):
        fetcher = self.make_fetcher()
        latin = response_connection(FakeResponse(200, {'Content-Type': 'text/html; charset=iso-8859-1'}, b'caf\xe9'))
        Harness(fetcher, {'example.com': [PUBLIC_A]}, [latin])
        result = fetcher.fetch('https://example.com/')
        self.assertEqual(result['text'], 'caf\xe9')
        self.assertEqual(result['content_type'], 'text/html')
        fetcher2 = self.make_fetcher()
        weird = response_connection(FakeResponse(200, {'Content-Type': 'text/plain; charset="x-unknown"'}, b'a\xffb'))
        Harness(fetcher2, {'example.com': [PUBLIC_A]}, [weird])
        result = fetcher2.fetch('https://example.com/')
        self.assertEqual(result['text'], 'a\ufffdb')
        fetcher3 = self.make_fetcher()
        plain = response_connection(FakeResponse(200, {}, b'raw'))
        Harness(fetcher3, {'example.com': [PUBLIC_A]}, [plain])
        result = fetcher3.fetch('https://example.com/')
        self.assertEqual(result['content_type'], '')
        self.assertEqual(result['text'], 'raw')

    def test_http_error_statuses_are_returned_not_raised(self):
        fetcher = self.make_fetcher()
        harness = Harness(fetcher, {'example.com': [PUBLIC_A]}, [
            response_connection(FakeResponse(404, {'Content-Type': 'application/json'}, b'{}')),
            response_connection(FakeResponse(500, {}, b'')),
        ])
        result = fetcher.fetch('https://example.com/missing')
        self.assertEqual(result['status'], 404)
        result = fetcher.fetch('https://example.com/broken')
        self.assertEqual(result['status'], 500)
        self.assertEqual(len(harness.connection_calls), 2)

    def test_invalid_configuration(self):
        for kwargs in ({'max_requests': 0}, {'max_bytes': 0}, {'budget_seconds': 0}, {'timeout_seconds': -1}):
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(NetworkError) as caught:
                    m.SafeFetcher(**kwargs)
                self.assertEqual(caught.exception.code, 'invalid_configuration')


if __name__ == '__main__':
    unittest.main()
