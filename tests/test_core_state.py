"""Persistent boundary tests; no real customer data or external tool execution."""
import builtins
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from backend.core_engine import state

try:
    import cryptography  # noqa: F401
    CRYPTO = True
except ImportError:
    CRYPTO = False


@unittest.skipUnless(CRYPTO, 'dedicated server venv with cryptography required')
class StateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'private' / 'core.sqlite'
        self.secret = b'private-synthetic-test-key-' + b'k' * 32
        self.store = state.Store(self.path, self.secret)
        self.other = state.Store(self.path, self.secret)
        self.actor = 'api-token:1'

    def execute(self, sid, payload, key=None, actor=None, runner=None, store=None):
        return (store or self.store).execute(sid, payload, actor or self.actor, key, runner)

    def put(self, key='key', value='value', idem='put00001', **extra):
        return self.execute('memory.set', {'key': key, 'value': value, **extra}, idem)

    def get(self, key='key', **extra):
        return self.execute('memory.get', {'key': key, **extra})['data']

    def define(self, steps=None, key='define01'):
        if steps is None:
            steps = [{'id': 'one', 'skill_id': 'ip.lookup', 'input': {'ip': '127.0.0.1'}}]
        return self.execute('workflow.define', {'name': 'Synthetic workflow', 'steps': steps}, key)['data']['workflow_id']

    def assert_code(self, code, fn):
        with self.assertRaises(state.StateError) as caught:
            fn()
        self.assertEqual(caught.exception.code, code)

    def test_private_permissions_and_restart_persistence(self):
        self.assertEqual(self.path.parent.stat().st_mode & 0o777, 0o700)
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)
        self.put(value={'items': [1, True, None], 'unicode': 'aman ✓'})
        restarted = state.Store(self.path, self.secret)
        out = restarted.execute('memory.get', {'key': 'key'}, self.actor)
        self.assertEqual(out['data']['value'], {'items': [1, True, None], 'unicode': 'aman ✓'})

    def test_actor_scope_isolation_in_two_instances(self):
        self.put(scope='private')
        self.assertFalse(self.get()['found'])
        self.assertTrue(self.get(scope='private')['found'])
        result = self.execute('memory.get', {'scope': 'private', 'key': 'key'}, actor='api-token:2', store=self.other)
        self.assertEqual(result['data'], {'found': False, 'value': None, 'expires_at': None})
        self.execute('memory.clear', {'scope': 'private', 'all': True}, 'clear002', actor='api-token:2')
        self.assertTrue(self.get(scope='private')['found'])
        self.assertEqual(self.execute('memory.search', {'scope': 'private', 'query': 'value'}, actor='api-token:2')['data']['total'], 0)

    def test_untrusted_actor_and_input_identity_are_rejected(self):
        for actor in ('root', 'api-token:0', 'api-token:-1', 'api-token:01',
                      'api-token:1\n', 'api-token:１２', 'api-token:' + '1' * 20, None):
            self.assert_code('invalid_principal', lambda: self.store.execute('memory.get', {'key': 'key'}, actor))
        for field in ('actor', 'principal', 'secret', 'tenant'):
            self.assert_code('invalid_input_fields', lambda: self.execute('memory.get', {'key': 'key', field: 'root'}))

    def test_idempotent_mutations_require_bounded_key(self):
        for token in (None, '', 'short', 'has space', 'a' * 129, '../request', 123):
            self.assert_code('idempotency_required', lambda: self.put(idem=token))
        self.assertFalse(self.get()['found'])

    def test_idempotency_replay_canonical_payload_and_conflict(self):
        first = self.put(value={'a': 1, 'b': 2})
        replay = self.other.execute('memory.set', {'value': {'b': 2, 'a': 1}, 'key': 'key'}, self.actor, 'put00001')
        self.assertFalse(first['replayed'])
        self.assertTrue(replay['replayed'])
        self.assertEqual(first['data'], replay['data'])
        self.assert_code('idempotency_conflict', lambda: self.put(value='different'))
        self.assert_code('idempotency_conflict', lambda: self.execute('memory.clear', {'scope': 'default', 'all': True}, 'put00001'))
        self.assertEqual(self.get()['value'], {'a': 1, 'b': 2})
        # The same caller-supplied token belongs independently to another actor.
        self.execute('memory.set', {'key': 'key', 'value': 'other'}, 'put00001', actor='api-token:2')
        self.assertEqual(self.execute('memory.get', {'key': 'key'}, actor='api-token:2')['data']['value'], 'other')

    def test_concurrent_idempotency_across_store_instances(self):
        barrier = threading.Barrier(6)
        def write(index):
            barrier.wait(timeout=5)
            return (self.store if index % 2 else self.other).execute(
                'memory.set', {'key': 'counter', 'value': 'once'}, self.actor, 'concurrent01')
        with ThreadPoolExecutor(max_workers=6) as pool:
            results = list(pool.map(write, range(6)))
        self.assertEqual(sum(not result['replayed'] for result in results), 1)
        with sqlite3.connect(self.path) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM memory').fetchone()[0], 1)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM idempotency').fetchone()[0], 1)

    def test_ttl_cleanup_does_not_refresh_value_on_replay(self):
        with patch.object(state.time, 'time', return_value=100000):
            first = self.put(ttl_seconds=60)
        with patch.object(state.time, 'time', return_value=100059):
            self.assertTrue(self.get()['found'])
        with patch.object(state.time, 'time', return_value=100060):
            self.assertFalse(self.get()['found'])
            replay = self.put(ttl_seconds=60)
            self.assertTrue(replay['replayed'])
            self.assertEqual(first['data']['expires_at'], replay['data']['expires_at'])
            self.assertFalse(self.get()['found'])
        with patch.object(state.time, 'time', return_value=100000 + state.IDEMPOTENCY_TTL + 1):
            self.assertFalse(self.put(ttl_seconds=60)['replayed'])

    def test_clear_requires_scope_and_true_and_replay_cannot_delete_new_data(self):
        for payload in ({'all': True}, {'scope': 'default'}, {'scope': 'default', 'all': False}, {'scope': 'default', 'all': 1}):
            with self.assertRaises(state.StateError):
                self.execute('memory.clear', payload, 'clear001')
        self.put()
        first = self.execute('memory.clear', {'scope': 'default', 'all': True}, 'clear001')
        self.assertEqual(first['data']['cleared'], 1)
        self.put(value='new', idem='put00002')
        replay = self.execute('memory.clear', {'scope': 'default', 'all': True}, 'clear001')
        self.assertTrue(replay['replayed'])
        self.assertEqual(self.get()['value'], 'new')

    def test_literal_search_and_pagination(self):
        self.put(key='alpha', value='literal [.*% match', idem='put00001')
        self.put(key='beta', value='plain match', idem='put00002')
        result = self.execute('memory.search', {'query': '[.*%'})['data']
        self.assertEqual([x['key'] for x in result['items']], ['alpha'])
        self.assertEqual(self.execute('memory.search', {'query': 'MATCH', 'limit': 1})['data']['next_offset'], 1)
        page = self.execute('memory.search', {'query': 'match', 'limit': 1, 'offset': 1})['data']
        self.assertEqual(page['items'][0]['key'], 'beta')
        self.assertFalse(page['has_more'])

    def test_search_response_budget_produces_next_page_not_oversized_response(self):
        for i in range(12):
            self.put(key='item%02d' % i, value='needle' + 'a' * 15900, idem='search%03d' % i)
        result = self.execute('memory.search', {'query': 'needle', 'limit': 20})
        self.assertLessEqual(len(json.dumps(result).encode()), state.MAX_OUTPUT_BYTES)
        self.assertTrue(result['data']['has_more'])
        self.assertGreater(result['data']['next_offset'], 0)
        self.assertLess(result['data']['next_offset'], 12)

    def test_value_limits_json_types_depth_and_nested_round_trip(self):
        value = 1
        for _ in range(7):
            value = [value]
        self.put(value=value)
        self.assertEqual(self.get()['value'], value)
        for value in ('a' * state.MAX_VALUE_BYTES, float('nan'), float('inf'), object(), {1: 'invalid key'}):
            with self.assertRaises(state.StateError):
                self.put(value=value, idem='invalid01')
        value = 1
        for _ in range(9):
            value = [value]
        self.assert_code('json_complexity', lambda: self.put(value=value, idem='invalid02'))
        for ttl in (False, 59, 604801):
            self.assert_code('invalid_ttl_seconds', lambda: self.put(ttl_seconds=ttl, idem='invalid03'))

    def test_record_actor_and_idempotency_caps_roll_back_mutation(self):
        with patch.object(state, 'MAX_RECORDS', 2):
            self.put(key='one', idem='record01')
            self.put(key='two', idem='record02')
            self.assert_code('state_record_capacity', lambda: self.put(key='three', idem='record03'))
            self.assertFalse(self.get('three')['found'])
        with patch.object(state, 'MAX_ACTORS', 1):
            self.assert_code('state_actor_capacity', lambda: self.execute('memory.set', {'key': 'other', 'value': 1}, 'actor002', actor='api-token:2'))
        with patch.object(state, 'MAX_IDEMPOTENCY_ACTOR', 2):
            self.assert_code('idempotency_capacity', lambda: self.put(key='one', value='changed', idem='record04'))
            self.assertEqual(self.get('one')['value'], 'value')
        with patch.object(state, 'MAX_IDEMPOTENCY_GLOBAL', 2):
            self.assert_code('idempotency_capacity', lambda: self.execute('memory.set', {'key': 'other', 'value': 1}, 'actor003', actor='api-token:2'))

    def test_actor_and_global_storage_budgets(self):
        self.put()
        with patch.object(state, 'MAX_ACTOR_BYTES', 800):
            self.assert_code('state_storage_capacity', lambda: self.put(key='large', value='x' * 700, idem='large001'))
        self.assertFalse(self.get('large')['found'])
        with patch.object(state, 'MAX_GLOBAL_BYTES', 800):
            self.assert_code('state_storage_capacity', lambda: self.execute('memory.set', {'key': 'large', 'value': 'x' * 700}, 'large002', actor='api-token:2'))

    def test_raw_database_contains_no_customer_or_identity_plaintext(self):
        marker = 'SECRET_customer_fixture_25_not_real_credential'
        self.put(key=marker, value={'message': marker}, scope='privatefixture', idem='requestsecret001')
        workflow_id = self.define([{'id': 'one', 'skill_id': 'nlu.spam.detect', 'input': {'text': marker}}])
        self.execute('workflow.run', {'workflow_id': workflow_id}, 'runsecret001',
                     runner=lambda sid, args: {'status': 'completed', 'data': marker})
        raw = self.path.read_bytes()
        for forbidden in (marker, 'privatefixture', 'Synthetic workflow', 'api-token:1', 'requestsecret001', 'runsecret001'):
            self.assertNotIn(forbidden.encode(), raw)
        self.assertEqual(self.get(marker, scope='privatefixture')['value']['message'], marker)

    def test_ciphertext_cannot_be_transplanted_between_actors(self):
        self.put(value='owner-secret')
        self.execute('memory.set', {'key': 'key', 'value': 'other'}, 'other001', actor='api-token:2')
        one = self.store._hash('actor', self.actor)
        two = self.store._hash('actor', 'api-token:2')
        with sqlite3.connect(self.path) as db:
            sealed = db.execute('SELECT sealed FROM memory WHERE actor=?', (one,)).fetchone()[0]
            db.execute('UPDATE memory SET sealed=? WHERE actor=?', (sealed, two))
        self.assert_code('state_integrity_failure', lambda: self.execute('memory.get', {'key': 'key'}, actor='api-token:2'))

    def test_wrong_secret_symlink_hardlink_and_unsafe_modes_fail_closed(self):
        with self.assertRaises(state.StateError):
            state.Store(self.path, b'different-secret-' + b'x' * 32)
        link = self.path.parent / 'link.sqlite'
        link.symlink_to(self.path)
        with self.assertRaises(state.StateError):
            state.Store(link, self.secret)
        hardlink = self.path.parent / 'hard.sqlite'
        os.link(self.path, hardlink)
        with self.assertRaises(state.StateError):
            state.Store(hardlink, self.secret)
        hardlink.unlink()
        os.chmod(self.path, 0o644)
        self.assert_code('unsafe_state_database', lambda: self.get())
        os.chmod(self.path, 0o600)
        os.chmod(self.path.parent, 0o755)
        self.assert_code('unsafe_state_directory', lambda: self.get())
        with self.assertRaises(state.StateError):
            state.Store(self.path, self.secret)
        os.chmod(self.path.parent, 0o700)

    def test_missing_crypto_creates_no_plaintext_fallback(self):
        original_import = builtins.__import__
        def no_crypto(name, *args, **kwargs):
            if name.startswith('cryptography'):
                raise ImportError('synthetic missing dependency')
            return original_import(name, *args, **kwargs)
        target = Path(self.tmp.name) / 'absent' / 'state.db'
        with patch('builtins.__import__', no_crypto):
            self.assert_code('state_crypto_unavailable', lambda: state.Store(target, self.secret))
        self.assertFalse(target.exists())

    def test_workflow_validation_rejects_unsafe_skills_dags_and_references(self):
        valid = {'id': 'one', 'skill_id': 'ip.lookup', 'input': {'ip': '127.0.0.1'}}
        bad = [[], [valid, valid], [{**valid, 'skill_id': 'shell.exec'}],
               [{**valid, 'depends_on': ['missing']}], [{**valid, 'depends_on': ['one']}],
               [{**valid, 'input': {'path': '/etc/passwd'}}],
               [{**valid, 'input': {'nested': {'url': 'https://private.invalid'}}}],
               [{**valid, 'input': {'text': '${steps.secret.output}'}}],
               [{**valid, 'actor': 'root'}],
               [{**valid, 'depends_on': ['two']}, {'id': 'two', 'skill_id': 'ip.lookup', 'input': {}, 'depends_on': ['one']}]]
        for steps in bad:
            with self.subTest(steps=steps), self.assertRaises(state.StateError):
                self.define(steps)
        self.assert_code('unknown_skill', lambda: self.execute('workflow.schedule', {}, 'schedule01'))

    def test_workflow_topological_execution_and_replay_preserve_real_results(self):
        workflow_id = self.define([
            {'id': 'second', 'skill_id': 'security.pii.mask', 'input': {'text': 'fixture'}, 'depends_on': ['first']},
            {'id': 'first', 'skill_id': 'ip.lookup', 'input': {'ip': '127.0.0.1'}},
        ])
        calls = []
        def runner(sid, payload):
            calls.append((sid, payload))
            return {'status': 'completed', 'data': {'observed_skill': sid}}
        result = self.execute('workflow.run', {'workflow_id': workflow_id}, 'run00001', runner=runner)
        self.assertEqual([c[0] for c in calls], ['ip.lookup', 'security.pii.mask'])
        self.assertEqual(result['data']['status'], 'completed')
        self.assertEqual([s['id'] for s in result['data']['steps']], ['first', 'second'])
        replay = self.execute('workflow.run', {'workflow_id': workflow_id}, 'run00001', store=self.other)
        self.assertTrue(replay['replayed'])
        self.assertEqual(replay['data'], result['data'])
        self.assertEqual(len(calls), 2)
        self.assert_code('workflow_not_found', lambda: self.execute('workflow.run', {'workflow_id': workflow_id}, 'run00001', actor='api-token:2', runner=runner))

    def test_failed_step_stops_workflow_and_replay_never_reexecutes(self):
        workflow_id = self.define([
            {'id': 'one', 'skill_id': 'ip.lookup', 'input': {}},
            {'id': 'two', 'skill_id': 'ip.lookup', 'input': {}, 'depends_on': ['one']},
        ])
        calls = []
        def fail(sid, payload):
            calls.append(sid)
            return {'status': 'failed', 'error': {'code': 'fixture_failure'}}
        result = self.execute('workflow.run', {'workflow_id': workflow_id}, 'failure01', runner=fail)
        self.assertEqual(result['data']['status'], 'failed')
        self.assertEqual([s['status'] for s in result['data']['steps']], ['failed', 'skipped'])
        self.assertEqual(result['data']['steps'][0]['result']['error']['code'], 'fixture_failure')
        replay = self.execute('workflow.run', {'workflow_id': workflow_id}, 'failure01', runner=fail)
        self.assertTrue(replay['replayed'])
        self.assertEqual(len(calls), 1)

    def test_runner_exception_and_excess_output_are_bounded_and_cached(self):
        workflow_id = self.define()
        def broken(sid, payload):
            raise RuntimeError('SENSITIVE_EXCEPTION_NOT_RETURNED')
        result = self.execute('workflow.run', {'workflow_id': workflow_id}, 'broken01', runner=broken)
        self.assertNotIn('SENSITIVE_EXCEPTION', json.dumps(result))
        large = self.execute('workflow.run', {'workflow_id': workflow_id}, 'largeout01',
            runner=lambda sid, payload: {'status': 'completed', 'data': 'x' * state.MAX_OUTPUT_BYTES})
        self.assertEqual(large['data']['status'], 'failed')
        self.assertLess(len(json.dumps(large).encode()), state.MAX_OUTPUT_BYTES)
        self.assertTrue(self.execute('workflow.run', {'workflow_id': workflow_id}, 'largeout01')['replayed'])

    def test_running_reservation_is_cross_instance_and_holds_no_db_lock(self):
        workflow_id = self.define()
        entered, release = threading.Event(), threading.Event()
        def runner(sid, payload):
            entered.set()
            self.assertTrue(release.wait(timeout=5))
            return {'status': 'completed', 'data': 'actual result'}
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(self.execute, 'workflow.run', {'workflow_id': workflow_id}, 'running01', runner=runner)
            try:
                self.assertTrue(entered.wait(timeout=5))
                again = self.execute('workflow.run', {'workflow_id': workflow_id}, 'running01', store=self.other)
                self.assertEqual(again['status'], 'in_progress')
                self.execute('memory.set', {'key': 'parallel', 'value': 1}, 'parallel01', actor='api-token:2', store=self.other)
            finally:
                release.set()
            self.assertEqual(future.result(timeout=5)['data']['status'], 'completed')

    def test_interrupted_run_becomes_recovery_required_and_never_reexecutes(self):
        workflow_id = self.define()
        calls = []
        def interrupted(sid, payload):
            calls.append(sid)
            raise KeyboardInterrupt('synthetic worker interruption')
        with patch.object(state.time, 'time', return_value=100000):
            with self.assertRaises(KeyboardInterrupt):
                self.execute('workflow.run', {'workflow_id': workflow_id}, 'interrupt01', runner=interrupted)
        for now in (100000 + state.RUN_LEASE_SECONDS, 100000 + 3 * state.IDEMPOTENCY_TTL):
            with patch.object(state.time, 'time', return_value=now):
                result = self.execute('workflow.run', {'workflow_id': workflow_id}, 'interrupt01', runner=interrupted, store=self.other)
                self.assertEqual(result['status'], 'recovery_required')
        self.assertEqual(len(calls), 1)

    def test_workflow_deadline_stops_before_next_step(self):
        workflow_id = self.define([
            {'id': 'one', 'skill_id': 'ip.lookup', 'input': {}},
            {'id': 'two', 'skill_id': 'ip.lookup', 'input': {}},
        ])
        calls = []
        def runner(sid, payload):
            calls.append(sid)
            return {'status': 'completed'}
        with patch.object(state.time, 'monotonic', side_effect=[0, 0, 16]):
            result = self.execute('workflow.run', {'workflow_id': workflow_id}, 'deadline01', runner=runner)
        self.assertEqual(result['data']['status'], 'failed')
        self.assertEqual(result['data']['steps'][0]['error'], 'workflow_deadline')
        self.assertEqual(result['data']['steps'][1]['status'], 'skipped')
        self.assertEqual(len(calls), 1)

    def test_workflow_result_capacity_reserved_before_any_runner_call(self):
        workflow_id = self.define()
        calls = []
        with patch.object(state, 'MAX_ACTOR_BYTES', 1000):
            self.assert_code('state_storage_capacity', lambda: self.execute('workflow.run',
                {'workflow_id': workflow_id}, 'reserve01', runner=lambda sid, p: calls.append(sid)))
        self.assertEqual(calls, [])
        with sqlite3.connect(self.path) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM idempotency WHERE state='running'").fetchone()[0], 0)

    def test_busy_database_fails_closed_with_bounded_timeout(self):
        db = sqlite3.connect(self.path)
        self.addCleanup(db.close)
        db.execute('BEGIN IMMEDIATE')
        started = time.monotonic()
        try:
            self.assert_code('state_busy_or_full', lambda: self.put())
        finally:
            db.rollback()
        self.assertLess(time.monotonic() - started, 3.5)

    def test_only_six_implemented_skills_are_advertised_and_schemas_detached(self):
        descriptors = state.descriptors()
        self.assertEqual(set(descriptors), {'memory.set', 'memory.get', 'memory.search',
            'memory.clear', 'workflow.define', 'workflow.run'})
        self.assertTrue(all(v['input_schema']['additionalProperties'] is False for v in descriptors.values()))
        descriptors['memory.set']['input_schema']['properties'].clear()
        self.assertIn('value', state.descriptors()['memory.set']['input_schema']['properties'])


if __name__ == '__main__':
    unittest.main()
