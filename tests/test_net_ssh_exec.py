"""Guard-rail tests for runtime/tools/net_ssh_exec.py (no network access)."""
import json
import subprocess
import sys
import unittest
from pathlib import Path

HELPER = Path(__file__).resolve().parents[1] / 'runtime' / 'tools' / 'net_ssh_exec.py'


def run(payload):
    proc = subprocess.run([sys.executable, '-I', str(HELPER)], input=payload,
                          capture_output=True, text=True, timeout=60)
    return proc.returncode, proc.stdout.strip()


class HelperGuardTests(unittest.TestCase):
    def test_invalid_json_is_rejected(self):
        code, out = run('not json')
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(out)['error'], 'invalid_request')

    def test_missing_fields_are_rejected(self):
        code, out = run(json.dumps({'host': 'example.invalid'}))
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(out)['error'], 'invalid_request')

    def test_result_is_single_json_object(self):
        code, out = run(json.dumps({'host': '127.0.0.1', 'port': 1, 'username': 'x',
                                    'password': 'y', 'command': 'true', 'timeout': 2}))
        payload = json.loads(out)
        self.assertIsInstance(payload, dict)
        self.assertIn('ok', payload)
        self.assertFalse(payload['ok'])
        self.assertNotIn('y', json.dumps(payload))
        self.assertIn(code, (3, 4, 5, 6))


if __name__ == '__main__':
    unittest.main()
