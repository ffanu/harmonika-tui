import copy
import unittest
from backend.config_transaction.engine import prepare, run, PlanError


def answer(code=0, **extra):
    return {'ok': code == 0, 'execution_state': 'completed', 'connection_status': 'connected',
            'exit_status': code, 'output': '', 'stderr': '', **extra}


def step(name):
    return {'command': 'apply ' + name, 'verify': 'check ' + name,
            'rollback': 'undo ' + name, 'rollback_verify': 'restored ' + name}


class Plans(unittest.TestCase):
    def test_quoted_and_escaped_semicolons_are_preserved(self):
        command = "printf '%s' 'ready;safe'"
        self.assertEqual(prepare({'target': 'demo', 'config': command})['steps'], [{'command': command}])
        self.assertEqual(prepare({'target': 'demo', 'config': r'printf a\;b; printf "c;d"'})['steps'],
                         [{'command': r'printf a\;b'}, {'command': 'printf "c;d"'}])

    def test_legacy_separators_and_multiline(self):
        p = prepare({'target': 'demo', 'config': '\nfirst;\nsecond\n'})
        self.assertEqual([s['command'] for s in p['steps']], ['first', 'second'])

    def test_complex_scripts_need_explicit_array_and_are_not_rewritten(self):
        for command in ['a # comment; b', 'a && b', 'echo $(a;b)', 'echo "$(a;b)"', 'cat <<EOF\na\nEOF']:
            with self.assertRaises(PlanError): prepare({'target': 'demo', 'config': command})
            self.assertEqual(prepare({'target': 'demo', 'config': [command]})['steps'][0]['command'], command)

    def test_malformed_entire_plan_is_rejected_before_any_execution(self):
        calls = []
        for config in [['valid', 42], ['ok', {'command': 'x', 'rollback': 'undo'}], "echo 'oops", [], ['a'] * 17, None]:
            r = run({'target': 'demo', 'config': config, 'dry_run': False}, lambda *a: calls.append(a))
            self.assertFalse(r['ok']); self.assertFalse(r['changes_applied'])
        self.assertEqual(calls, [])

    def test_preview_is_default_and_bool_is_strict(self):
        r = run({'target': 'demo', 'config': 'touch fixture'}, None)
        self.assertTrue(r['ok']); self.assertEqual(r['state'], 'preview'); self.assertFalse(r['execution_performed'])
        self.assertEqual(run({'target': 'demo', 'config': 'x', 'dry_run': 'false'}, None)['state'], 'rejected')

    def test_hash_is_bound_to_complete_plan(self):
        a = {'target': 'demo', 'config': [step('a')]}
        b = copy.deepcopy(a); b['config'][0]['verify'] = 'different'
        self.assertNotEqual(prepare(a)['plan_sha256'], prepare(b)['plan_sha256'])


class Transactions(unittest.TestCase):
    def test_success_requires_post_checks_to_claim_verified(self):
        calls = []
        r = run({'target': 'demo', 'config': [step('a')], 'dry_run': False},
                lambda command, *a: calls.append(command) or answer())
        self.assertEqual(calls, ['apply a', 'check a']); self.assertEqual(r['state'], 'verified')
        self.assertTrue(r['verified'])
        r = run({'target': 'demo', 'config': ['apply a'], 'dry_run': False}, lambda *a: answer())
        self.assertEqual(r['state'], 'applied_unverified'); self.assertFalse(r['verified'])

    def test_verify_failure_rolls_back_in_reverse_including_latest_step(self):
        calls = []
        def executor(command, *args):
            calls.append(command); return answer(7 if command == 'check b' else 0)
        r = run({'target': 'demo', 'config': [step('a'), step('b'), step('c')], 'dry_run': False}, executor)
        self.assertEqual(calls, ['apply a', 'check a', 'apply b', 'check b', 'undo b', 'restored b', 'undo a', 'restored a'])
        self.assertFalse(r['ok']); self.assertEqual(r['state'], 'rolled_back'); self.assertTrue(r['rollback_verified'])
        self.assertFalse(r['changes_applied'])

    def test_nonzero_apply_can_have_partial_side_effects(self):
        calls = []
        def execute(command, *args):
            calls.append(command); return answer(255 if command == 'apply a' else 0)
        r = run({'target': 'demo', 'config': [step('a')], 'dry_run': False}, execute)
        self.assertEqual(calls, ['apply a', 'undo a', 'restored a']); self.assertEqual(r['state'], 'rolled_back')

    def test_unknown_timeout_never_retries_or_compensates(self):
        calls = []
        def execute(command, *args):
            calls.append(command)
            return {'ok': False, 'exit_status': None, 'connection_status': 'connected', 'execution_state': 'unknown'}
        r = run({'target': 'demo', 'config': [step('a'), step('b')], 'dry_run': False}, execute)
        self.assertEqual(calls, ['apply a']); self.assertEqual(r['state'], 'needs_reconciliation')
        self.assertFalse(r['rollback_attempted']); self.assertFalse(r['safe_to_retry'])

    def test_without_rollback_plan_no_compensation_is_invented(self):
        calls = []
        r = run({'target': 'demo', 'config': ['a', 'b'], 'dry_run': False},
                lambda command, *a: calls.append(command) or answer(1))
        self.assertEqual(calls, ['a']); self.assertEqual(r['state'], 'failed_partial')

    def test_failed_rollback_does_not_claim_restoration(self):
        def execute(command, *args): return answer(1 if command.startswith(('check', 'undo')) else 0)
        r = run({'target': 'demo', 'config': [step('a')], 'dry_run': False}, execute)
        self.assertEqual(r['state'], 'rollback_failed'); self.assertFalse(r['rollback_verified'])

    def test_truncated_verification_is_incomplete_not_verified(self):
        def execute(command, *args): return answer(output='x' * 1500 if command.startswith('check') else '')
        r = run({'target': 'demo', 'config': [step('a')], 'dry_run': False}, execute)
        self.assertEqual(r['state'], 'rolled_back'); self.assertFalse(r['results'][1]['ok'])

    def test_callback_exception_is_uncertain_and_private_text_not_exposed(self):
        def execute(*a): raise RuntimeError('private-password-and-customer-text')
        r = run({'target': 'demo', 'config': [step('a')], 'dry_run': False}, execute)
        self.assertEqual(r['state'], 'needs_reconciliation'); self.assertNotIn('private-password', str(r))


if __name__ == '__main__': unittest.main()
