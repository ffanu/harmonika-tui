import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from backend.config_transaction import engine

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('config_client', ROOT / 'runtime/tools/config_transaction_client.py')
with patch.dict(sys.modules, {'config_transaction_engine': engine}):
    client = importlib.util.module_from_spec(spec); spec.loader.exec_module(client)


def result(code=0):
    return {'ok': code == 0, 'exit_status': code, 'execution_state': 'completed',
            'connection_status': 'connected', 'output': 'private-fixture-output', 'stderr': ''}


def command(state):
    script = 'import os,runpy;os.environ["HARMONIKA_CONFIG_STATE"]=' + repr(state) + ';runpy.run_path(' + repr(str(ROOT / 'backend/config_transaction/worker.py')) + ',run_name="__main__")'
    return [sys.executable, '-I', '-c', script]


class Transport(unittest.TestCase):
    def test_release_bundle_uses_canonical_engine_and_imports_in_isolation(self):
        spec = importlib.util.spec_from_file_location('package_builder', ROOT / 'runtime/package-release.py')
        builder = importlib.util.module_from_spec(spec);spec.loader.exec_module(builder)
        with tempfile.TemporaryDirectory() as root:
            tools = Path(root)
            builder.copy_config_transaction_tools(tools)
            self.assertEqual((tools / 'config_transaction_engine.py').read_bytes(),
                             (ROOT / 'backend/config_transaction/engine.py').read_bytes())
            script = 'import sys,json;sys.path.insert(0,sys.argv[1]);from config_transaction_client import prepare;print(json.dumps(prepare({"target":"fixture","config":"echo quoted"})))'
            r = subprocess.run([sys.executable, '-I', '-c', script, root], capture_output=True, text=True, check=True)
            self.assertTrue(json.loads(r.stdout)['dry_run'])

    def test_adapter_rejects_unknown_disabled_and_malformed_targets_before_transport(self):
        for args, targets in [(None, {}), ({'target': 'missing'}, {}),
                              ({'target': 'disabled'}, {'disabled': {'enabled': False}})]:
            with patch.object(client, 'exchange') as exchange:
                r = client.invoke(args, lambda: targets, lambda *a: self.fail('Unexpected SSH'))
                self.assertFalse(r['ok']);exchange.assert_not_called()

    def test_credentials_stay_in_local_connector_not_plan(self):
        args = {'target': 'demo', 'config': ['approved'], 'dry_run': False}
        info = {'host': '127.0.0.1', 'password': 'private-fixture-key', 'key_path': '/fixture/key'}
        calls = []
        def exchange(received, target, executor):
            self.assertEqual(received, args);self.assertNotIn('password', received)
            return executor('approved', 2)
        with patch.object(client, 'exchange', side_effect=exchange):
            r = client.invoke(args, lambda: {'demo': info}, lambda *a, **kw: calls.append((a, kw)) or result())
        self.assertTrue(r['ok']);self.assertEqual(calls[0][0][3], 'private-fixture-key')

    def test_actual_worker_preview_does_not_call_connector(self):
        with tempfile.TemporaryDirectory() as state:
            calls = []
            r = client.exchange({'target': 'demo', 'config': "printf '%s' 'ready;safe'"},
                                {'host': '127.0.0.1', 'port': 22}, lambda *a: calls.append(a), command(state))
            self.assertEqual(r['state'], 'preview'); self.assertEqual(calls, [])
            self.assertEqual(r['commands'], ["printf '%s' 'ready;safe'"])

    def test_actual_worker_runs_and_rolls_back_only_requested_steps(self):
        with tempfile.TemporaryDirectory() as state:
            calls = []
            def execute(cmd, timeout):
                calls.append(cmd); return result(1 if cmd == 'check' else 0)
            args = {'target': 'demo', 'dry_run': False, 'config': [
                {'command': 'apply', 'verify': 'check', 'rollback': 'undo', 'rollback_verify': 'restored'}]}
            r = client.exchange(args, {'host': '127.0.0.1'}, execute, command(state))
            self.assertEqual(calls, ['apply', 'check', 'undo', 'restored'])
            self.assertEqual(r['state'], 'rolled_back'); self.assertTrue(r['rollback_verified'])
            db = sqlite3.connect(str(Path(state) / 'mechanics.sqlite'))
            columns = [x[1] for x in db.execute('pragma table_info(mechanics)')]
            self.assertEqual(columns, ['timestamp', 'skill_id', 'error_type', 'latency'])
            rows = db.execute('select * from mechanics').fetchall();db.close()
            self.assertNotIn('private-fixture-output', str(rows)); self.assertNotIn('apply', str(rows))
            self.assertEqual(Path(state, 'mechanics.sqlite').stat().st_mode & 0o777, 0o600)

    def test_same_endpoint_is_locked_while_a_transaction_is_active(self):
        with tempfile.TemporaryDirectory() as state:
            args = {'target': 'first', 'config': ['a'], 'dry_run': False}
            envelope = {'input': args, 'scope_id': __import__('hashlib').sha256(b'127.0.0.1:22').hexdigest()}
            p = subprocess.Popen(command(state), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            try:
                p.stdin.write(json.dumps(envelope) + '\n');p.stdin.flush()
                first = json.loads(p.stdout.readline());self.assertEqual(first['type'], 'execute')
                calls = []
                r = client.exchange({**args, 'target': 'second'}, {'host': '127.0.0.1', 'port': 22},
                                    lambda *a: calls.append(a), command(state))
                self.assertEqual(r['error'], 'target_busy');self.assertEqual(calls, [])
                p.stdin.write(json.dumps({'id': first['id'], 'result': result()}) + '\n');p.stdin.flush()
                self.assertEqual(json.loads(p.stdout.readline())['result']['state'], 'applied_unverified')
            finally:
                p.stdin.close();p.wait(timeout=5);p.stdout.close();p.stderr.close()

    def test_valid_bounded_unicode_results_survive_final_response(self):
        with tempfile.TemporaryDirectory() as state:
            args = {'target': 'demo', 'dry_run': False,
                    'config': [{'command': 'apply', 'verify': 'check'} for _ in range(16)]}
            def execute(*a):
                return {**result(), 'output': '\U0001f310' * 1000, 'stderr': '\U0001f310' * 1000}
            r = client.exchange(args, {'host': '127.0.0.1'}, execute, command(state))
            self.assertEqual(r['state'], 'verified');self.assertEqual(len(r['results']), 32)

    def malicious(self, message):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root, 'fake.py');path.write_text('import json,sys\nsys.stdin.readline()\nprint(' + repr(json.dumps(message)) + ',flush=True)\nsys.stdin.read()\n')
            calls = []
            r = client.exchange({'target': 'demo', 'config': ['approved'], 'dry_run': False},
                                {'host': '127.0.0.1'}, lambda *a: calls.append(a), [sys.executable, '-I', str(path)])
            self.assertEqual(calls, []);return r

    def test_unrequested_command_cannot_reach_connector(self):
        r = self.malicious({'type': 'execute', 'id': 1, 'index': 0, 'phase': 'command', 'command': 'unapproved', 'timeout': 2})
        self.assertEqual(r['error'], 'config_coordinator_transport_failed');self.assertFalse(r['changes_applied'])

    def test_success_without_any_execution_evidence_is_rejected(self):
        plan = engine.prepare({'target': 'demo', 'config': ['approved'], 'dry_run': False})
        r = self.malicious({'type': 'result', 'result': {'ok': True, 'state': 'applied_unverified', 'plan_sha256': plan['plan_sha256']}})
        self.assertFalse(r['ok'])

    def test_duplicate_mutation_request_is_not_executed_twice(self):
        with tempfile.TemporaryDirectory() as root:
            request = {'type': 'execute', 'id': 1, 'index': 0, 'phase': 'command', 'command': 'approved', 'timeout': 2}
            duplicate = {**request, 'id': 2}
            source = 'import sys\nsys.stdin.readline()\nprint(' + repr(json.dumps(request)) + ',flush=True)\nsys.stdin.readline()\nprint(' + repr(json.dumps(duplicate)) + ',flush=True)\nsys.stdin.read()\n'
            path = Path(root, 'fake.py');path.write_text(source)
            calls = []
            r = client.exchange({'target': 'demo', 'config': ['approved'], 'dry_run': False},
                                {'host': '127.0.0.1'}, lambda command, *a: calls.append(command) or result(),
                                [sys.executable, '-I', str(path)])
            self.assertEqual(calls, ['approved']);self.assertFalse(r['ok']);self.assertFalse(r['safe_to_retry'])


if __name__ == '__main__': unittest.main()
