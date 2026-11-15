import importlib.util
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

TOOLS = Path(__file__).resolve().parents[1]/'runtime/tools'
sys.path.insert(0, str(TOOLS))
import project_memory_mcp as client


class MemoryMCP(unittest.TestCase):
    def test_explicit_approved_note_uses_existing_authenticated_gateway(self):
        args = {'action': 'set', 'project_id': 'sample', 'consent': True, 'key': 'build', 'value': {'kind': 'verified_command', 'text': 'npm test'}, 'idempotency_key': 'synthetic-01'}
        out = {'status': 'completed', 'skill_id': 'memory.set', 'data': {'stored': True}}
        with patch.object(client, 'request', return_value=out) as request:
            result = client.invoke(args)
        self.assertTrue(result['ok']);self.assertTrue(result['untrusted_data'])
        self.assertEqual(request.call_args.kwargs['base'], 'https://chat.harmonika.id/v1/core')
        self.assertNotIn('actor', request.call_args.args[2])
        self.assertNotIn('idempotency_key', request.call_args.args[2]['input'])

    def test_no_consent_bad_action_identity_or_new_replay_key_rejected_locally(self):
        with patch.object(client, 'request') as request:
            for args in ({'action': 'set', 'project_id': 'a'}, {'action': 'get', 'project_id': 'a', 'actor': 'root'}, {'action': [], 'project_id': 'a'}, {'action': 'set', 'project_id': 'a', 'idempotency_key': 'synthetic-01', 'consent': False}):
                self.assertFalse(client.invoke(args)['ok'])
        request.assert_not_called()

    def test_unavailable_and_provider_error_never_echo_private_payload(self):
        with patch.object(client, 'request', side_effect=RuntimeError('synthetic-private')):
            self.assertEqual(client.invoke({'action': 'get', 'project_id': 'a', 'key': 'build'})['error'], 'project_memory_unavailable')
        with patch.object(client, 'request', return_value={'error': {'code': 'sensitive_content_rejected'}}):
            self.assertEqual(client.invoke({'action': 'get', 'project_id': 'a', 'key': 'build'})['error'], 'sensitive_content_rejected')

if __name__ == '__main__':
    unittest.main()
