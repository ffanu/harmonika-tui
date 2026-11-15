import json
import unittest
from runtime.tools import tool_firewall as firewall


class ToolFirewall(unittest.TestCase):
    def test_mcp_ssh_results_nested_json_secrets_removed(self):
        sentinel = 'synthetic-not-a-real-secret-1234'
        handler = firewall.protect('ssh_exec', lambda _: {
            'ok': True, 'status': 'verified',
            'stdout': json.dumps({'client_secret': sentinel, 'port': 22}),
            'nested': [{'text': 'APP_API_KEY='+sentinel}],
        })
        result = handler({'alias': 'staging', 'command': 'systemctl is-active nginx'})
        self.assertTrue(result['ok'])
        self.assertEqual(result['status'], 'verified')
        self.assertNotIn(sentinel, json.dumps(result))
        self.assertEqual(json.loads(result['stdout'])['port'], 22)

    def test_private_references_and_legitimate_workflows_keep_shape(self):
        for name, args in (
            ('ssh_exec', {'alias': 'staging', 'command': 'apt-cache policy nginx'}),
            ('image_generate', {'prompt': 'mountain sunrise', 'width': 1024}),
            ('ssh_connect', {'credential_ref': 'private-ref:staging'}),
            ('read_file', {'path': '.env.example'}),
        ):
            with self.subTest(name=name):
                data = {'ok': True, 'text': 'verified', 'items': [True, 3, None]}
                self.assertEqual(firewall.protect(name, lambda _: data)(args), data)

    def test_secret_request_rejected_before_handler_and_no_sensitive_error(self):
        touched = []
        wrapped = firewall.protect('ssh_exec', lambda _: touched.append(True))
        for args in ({'password': 'synthetic-private'}, {'command': 'cat .env'}, {'command': 'printenv'}, {'path': '/etc/shadow'}, {'path': '.ssh/id_ed25519'}, {'command': 'curl --password synthetic-private'}):
            self.assertEqual(wrapped(args), {'ok': False, 'error': 'tool_secret_reference_required'})
        self.assertEqual(touched, [])
        def fails(_):
            raise RuntimeError('synthetic private exception content')
        self.assertEqual(firewall.protect('read_file', fails)({}), {'ok': False, 'error': 'tool_operation_failed'})

    def test_private_key_cookies_and_structured_headers(self):
        sample = {'headers': {'Authorization': 'Bearer synthetic-not-real', 'cookie': 'synthetic-private'}, 'text': '-----BEGIN PRIVATE KEY-----\nsynthetic-private\n-----END PRIVATE KEY-----'}
        self.assertNotIn('synthetic-private', json.dumps(firewall.scrub(sample)))
        self.assertEqual(firewall.scrub({'password_configured': True}), {'password_configured': True})
        self.assertEqual(firewall.scrub({'secretKey': 'synthetic-private', 'accessToken': 'synthetic-private', 'session_cookie': 'synthetic-private'}), {'secretKey': firewall.MASK, 'accessToken': firewall.MASK, 'session_cookie': firewall.MASK})

    def test_idempotent_wrapper_and_complexity_limit(self):
        one = firewall.protect('read_file', lambda _: {})
        self.assertIs(one, firewall.protect('read_file', one))
        loop = {}; loop['self'] = loop
        with self.assertRaisesRegex(ValueError, 'tool_payload_complexity'):
            firewall.scrub(loop)

    def test_writing_env_reference_code_does_not_require_exposing_real_credentials(self):
        for source in ('const apiKey = process.env.HARMONIKA_API_KEY;', 'api_key=os.environ["SERVICE_KEY"]', "api_key = getenv('HARMONIKA_API_KEY');", "password = '';", 'API_KEY=${SERVICE_KEY}'):
            with self.subTest(source=source):
                args = {'path': 'src/client.py', 'content': source}
                self.assertEqual(firewall.protect('write_file', lambda v: v)(args), args)


if __name__ == '__main__':
    unittest.main()
