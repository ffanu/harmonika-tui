import copy
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from backend.config_transaction import engine

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'runtime/tools'))
import desktop_browser as browser
import browser_macro as macro
with patch.dict(sys.modules, {'config_transaction_engine': engine}):
    import config_transaction_client as client


def request(**extra):
    return {'action': 'macro', 'tab_id': '123', 'start_url': 'https://example.test/devices',
            'steps': [{'action': 'read', 'expected_text': 'Ready'}], **extra}


def mock_transport(plan, info, callback, **kw):
    return engine.run(plan, lambda cmd, timeout, index, phase: callback(cmd, timeout))


class BrowserMacroTests(unittest.TestCase):
    def test_complete_plan_validated_before_transport(self):
        transport = unittest.mock.Mock()
        for bad in [request(steps=[{'action': 'click', 'label': 'Ready'}]),
                    request(steps=[{'action': 'shell', 'expected_text': 'Ready'}]),
                    request(steps=[{'action': 'navigate', 'url': 'https://other.test', 'expected_text': 'Ready'}]),
                    request(start_url='https://user:password@example.test'), request(dry_run='false'),
                    request(steps=[{'action': 'read', 'expected_text': 'Ready'}] * 13)]:
            self.assertEqual(macro.invoke(bad, None, None, transport)['state'], 'rejected')
        transport.assert_not_called()

    def test_coordinator_receives_hashes_not_private_data(self):
        plan, _ = macro.compile_plan(request(steps=[{'action': 'type', 'label': 'Cari data', 'text': 'PRIVATE-SENTINEL', 'expected_text': 'PRIVATE-SENTINEL'}]))
        serialized = json.dumps(plan)
        for private in ('PRIVATE-SENTINEL', 'Cari data', 'example.test'):
            self.assertNotIn(private, serialized)

    def test_preview_never_observes_or_dispatches(self):
        calls = []
        r = macro.invoke(request(), lambda *a: calls.append(a), lambda *a: calls.append(a), mock_transport)
        self.assertEqual(r['state'], 'preview'); self.assertEqual(calls, [])

    def test_scope_changed_stops_before_any_action(self):
        calls = []
        r = macro.invoke(request(dry_run=False), lambda *a: calls.append(a),
                         lambda *a: {'ok': True, 'url': 'https://other.test'}, mock_transport)
        self.assertFalse(r['ok']); self.assertEqual(r['error'], 'macro_scope_changed'); self.assertEqual(calls, [])

    def test_delayed_postcondition_reads_without_reclick(self):
        calls, observed = [], 0
        def desktop(args):
            nonlocal observed
            calls.append(args['action'])
            if args['action'] == 'click': return {'ok': True, 'action_sent': True}
            observed += 1
            return {'ok': True, 'ready': True, 'main_text': 'Ready' if observed >= 3 else 'Loading',
                    'view_id': 'v', 'controls': [{'label': 'Preview', 'ref': '0', 'disabled': False}]}
        r = macro.invoke(request(dry_run=False, steps=[{'action': 'click', 'label': 'Preview', 'expected_text': 'Ready'}]),
                         desktop, lambda *a: {'ok': True, 'url': request()['start_url']}, mock_transport)
        self.assertEqual(r['state'], 'verified'); self.assertEqual(calls.count('click'), 1)

    def test_ambiguous_control_fails_without_action(self):
        calls = []
        def desktop(args):
            calls.append(args['action'])
            return {'ok': True, 'ready': True, 'controls': [{'label': 'Preview'}, {'label': 'Preview'}]}
        r = macro.invoke(request(dry_run=False, steps=[{'action': 'click', 'label': 'Preview', 'expected_text': 'Ready'}]),
                         desktop, lambda *a: {'ok': True, 'url': request()['start_url']}, mock_transport)
        self.assertEqual(r['error'], 'ambiguous_control'); self.assertEqual(calls, ['snapshot'])

    def test_existing_text_is_not_proof_of_click_completion(self):
        calls = []
        def desktop(args):
            calls.append(args['action'])
            return {'ok': True, 'ready': True, 'main_text': 'Ready',
                    'controls': [{'label': 'Preview', 'ref': '0'}]}
        r = macro.invoke(request(dry_run=False, steps=[{'action': 'click', 'label': 'Preview', 'expected_text': 'Ready'}]),
                         desktop, lambda *a: {'ok': True, 'url': request()['start_url']}, mock_transport)
        self.assertEqual(r['error'], 'postcondition_already_satisfied_choose_new_result')
        self.assertEqual(calls, ['snapshot']);self.assertEqual(r['actions_dispatched'], 0)

    def test_uncertain_dispatch_does_not_retry(self):
        calls = []
        def desktop(args):
            calls.append(args['action'])
            if args['action'] == 'click': raise browser.BrowserError('browser_action_state_unknown_do_not_retry')
            return {'ok': True, 'ready': True, 'view_id': 'v', 'controls': [{'label': 'Preview', 'ref': '0'}]}
        r = macro.invoke(request(dry_run=False, steps=[{'action': 'click', 'label': 'Preview', 'expected_text': 'Ready'}]),
                         desktop, lambda *a: {'ok': True, 'url': request()['start_url']}, mock_transport)
        self.assertEqual(r['state'], 'needs_reconciliation'); self.assertEqual(calls.count('click'), 1)

    def test_wrong_filter_value_cannot_claim_verified(self):
        clock = iter(range(0, 200, 2))
        def desktop(args):
            return {'ok': True, 'ready': True, 'view_id': 'v', 'action_sent': args['action'] == 'select',
                    'main_text': 'Ready', 'controls': [{'label': 'Status', 'ref': '0', 'value': 'online'}]}
        with patch.object(macro.time, 'monotonic', side_effect=lambda: next(clock)), patch.object(macro.time, 'sleep'):
            r = macro.invoke(request(dry_run=False, steps=[{'action': 'select', 'label': 'Status', 'value': 'offline', 'expected_text': 'Ready'}]),
                             desktop, lambda *a: {'ok': True, 'url': request()['start_url']}, mock_transport)
        self.assertFalse(r['ok']); self.assertEqual(r['error'], 'postcondition_timeout')

    def test_actual_worker_protocol_and_private_journal(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            shutil.copy(ROOT / 'backend/browser_macro/worker.py', root / 'worker.py')
            shutil.copy(ROOT / 'backend/config_transaction/engine.py', root / 'engine.py')
            script = 'import os,runpy;os.environ["HARMONIKA_BROWSER_STATE"]=' + repr(str(root / 'state')) + ';runpy.run_path(' + repr(str(root / 'worker.py')) + ',run_name="__main__")'
            def transport(plan, info, callback, **kw):
                return client.exchange(plan, info, callback, [sys.executable, '-I', '-c', script])
            r = macro.invoke(request(dry_run=False), lambda *a: {'ok': True, 'ready': True, 'main_text': 'Ready PRIVATE-SENTINEL'},
                             lambda *a: {'ok': True, 'url': request()['start_url']}, transport)
            self.assertEqual(r['state'], 'verified'); self.assertEqual(len(r['receipts']), 1)
            db = sqlite3.connect(root / 'state/mechanics.sqlite')
            self.assertEqual(db.execute('pragma journal_mode').fetchone()[0], 'wal')
            self.assertEqual([r[1] for r in db.execute('pragma table_info(mechanics)')], ['timestamp', 'skill_id', 'error_type', 'latency'])
            rows = db.execute('select * from mechanics').fetchall();db.close()
            self.assertEqual({r[1] for r in rows}, {'desktop.macro'})
            self.assertNotIn('PRIVATE-SENTINEL', str(rows)); self.assertNotIn('example.test', str(rows))

    def test_worker_rejects_shell_payload_before_execution(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            shutil.copy(ROOT / 'backend/browser_macro/worker.py', root / 'worker.py')
            shutil.copy(ROOT / 'backend/config_transaction/engine.py', root / 'engine.py')
            plan, _ = macro.compile_plan(request(dry_run=False))
            plan['config'][0]['command'] = 'touch injected'
            script = 'import os,runpy;os.environ["HARMONIKA_BROWSER_STATE"]=' + repr(str(root / 'state')) + ';runpy.run_path(' + repr(str(root / 'worker.py')) + ',run_name="__main__")'
            r = subprocess.run([sys.executable, '-I', '-c', script], input=json.dumps({'input': plan, 'scope_id': 'a' * 64}) + '\n', text=True, capture_output=True, check=True)
            self.assertNotIn('"type": "execute"', r.stdout)
            self.assertFalse(json.loads(r.stdout)['result']['ok'])

    def test_release_copies_browser_dependencies(self):
        spec = importlib.util.spec_from_file_location('builder', ROOT / 'runtime/package-release.py')
        builder = importlib.util.module_from_spec(spec); spec.loader.exec_module(builder)
        with tempfile.TemporaryDirectory() as folder:
            builder.copy_browser_macro_tools(Path(folder));builder.copy_config_transaction_tools(Path(folder))
            for name in ('desktop_browser.py','browser_audit.py','browser_macro.py','config_transaction_client.py','config_transaction_engine.py','play_console.py','play_console_engine.py'):
                self.assertTrue((Path(folder) / name).is_file())
            script='import sys;sys.path.insert(0,sys.argv[1]);import desktop_browser,play_console;assert "play_console" in desktop_browser.TOOLS[0]["inputSchema"]["properties"]["action"]["enum"];assert play_console.VERSION'
            subprocess.run([sys.executable,'-I','-c',script,folder],check=True,capture_output=True,text=True)


if __name__ == '__main__': unittest.main()
