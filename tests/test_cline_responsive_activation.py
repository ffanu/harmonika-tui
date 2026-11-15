import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

SOURCE = Path(__file__).resolve().parents[1]/'scripts/activate-cline-responsive.py'
spec = importlib.util.spec_from_file_location('activation', SOURCE)
activation = importlib.util.module_from_spec(spec)
spec.loader.exec_module(activation)


class ActivationGuards(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.base = self.root/'live'
        self.base.mkdir()
        candidate = self.root/'backend/planner'
        candidate.mkdir(parents=True)
        for n in activation.FILES:
            (self.base/n).write_text('old')
            (candidate/n).write_text('candidate')
        self.write('expected-baseline.json', {n: activation.digest(self.base/n) for n in activation.FILES})
        self.hashes = {n: activation.digest(candidate/n) for n in activation.FILES}
        self.write('verification.json', {'local_passed': True, 'integration_passed': True, 'activation_guards_passed': True, 'candidate_sha256': self.hashes})
        self.native = {'production': False, 'synthetic_only': True, 'passed': True, 'source_sha256': self.hashes,
                       'rows': [{'name': n, 'passed': True} for n in (
                           'autonomous_fixture_read_edit_test', 'transport_success_is_not_snapshot_success', 'large_context_retained')]}
        self.write('responsive-candidate.json', self.native)

    def write(self, name, value):
        (self.root/name).write_text(json.dumps(value))

    def test_exact_verified_source_allowed(self):
        self.assertEqual(activation.verify(self.root, self.base)[1], self.hashes)

    def test_current_operator_change_is_not_overwritten(self):
        (self.base/activation.FILES[0]).write_text('operator edit')
        with self.assertRaisesRegex(RuntimeError, 'baseline_changed'):
            activation.verify(self.root, self.base)

    def test_candidate_changed_after_model_probe_rejected(self):
        (self.root/'backend/planner'/activation.FILES[0]).write_text('later edit')
        with self.assertRaisesRegex(RuntimeError, 'candidate_changed'):
            activation.verify(self.root, self.base)

    def test_missing_failed_duplicate_or_production_canary_rejected(self):
        for change in ({'production': True}, {'synthetic_only': False}, {'passed': False},
                       {'rows': self.native['rows'][:2]}, {'rows': [self.native['rows'][0]]*3}):
            with self.subTest(change=change):
                self.write('responsive-candidate.json', {**self.native, **change})
                with self.assertRaisesRegex(RuntimeError, 'candidate_canary_required'):
                    activation.verify(self.root, self.base)

    def test_test_verification_required(self):
        self.write('verification.json', {'local_passed': True, 'integration_passed': False, 'candidate_sha256': self.hashes})
        with self.assertRaisesRegex(RuntimeError, 'verification_required'):
            activation.verify(self.root, self.base)

    def test_activation_guard_tests_required(self):
        self.write('verification.json', {'local_passed': True, 'integration_passed': True, 'candidate_sha256': self.hashes})
        with self.assertRaisesRegex(RuntimeError, 'verification_required'):
            activation.verify(self.root, self.base)

    def test_staged_helper_is_checked_before_executing_python(self):
        scripts = self.root/'scripts'
        scripts.mkdir()
        sentinel = self.root/'must-not-exist'
        (scripts/'activate-debug-workflow.py').write_text('raise RuntimeError("untrusted_code_executed")')
        with self.assertRaisesRegex(RuntimeError, 'activation_guards_changed'):
            activation.load_guards(self.root)
        self.assertFalse(sentinel.exists())

    def test_partial_rollback_restores_ours_and_preserves_operator_change(self):
        backup = self.root/'backup'
        backup.mkdir()
        for n in activation.FILES:
            (backup/n).write_text('old')
            (self.base/n).write_text('candidate')
        (self.base/activation.FILES[0]).write_text('operator change')
        conflicts = activation.restore_owned(activation.FILES, self.hashes, backup, self.base,
                                            {n: 0o600 for n in activation.FILES},
                                            lambda source, target, mode: target.write_bytes(source.read_bytes()))
        self.assertEqual(conflicts, [activation.FILES[0]])
        self.assertEqual((self.base/activation.FILES[0]).read_text(), 'operator change')
        for n in activation.FILES[1:]:
            self.assertEqual((self.base/n).read_text(), 'old')


if __name__ == '__main__':
    unittest.main()
