import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('level15_activation', Path(__file__).resolve().parents[1]/'scripts/level15-activate.py')
activation = importlib.util.module_from_spec(spec)
spec.loader.exec_module(activation)


class Level15Activation(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.base = self.root/'live'
        self.base.mkdir()
        self.candidate = self.root/'backend/planner'
        self.candidate.mkdir(parents=True)
        for n in activation.FILES:
            if n != 'task-continuity.cjs':
                (self.base/n).write_text('old')
            (self.candidate/n).write_text('candidate')
        self.expected = {n: activation.digest(self.base/n) for n in activation.FILES}
        self.hashes = {n: activation.digest(self.candidate/n) for n in activation.FILES}
        self.put('expected-baseline.json', self.expected)
        self.put('verification.json', {'local_passed': True, 'integration_passed': True,
                                      'activation_guards_passed': True, 'candidate_sha256': self.hashes})
        self.native = {'production': False, 'synthetic_only': True, 'passed': True,
                       'independent_operator_proof': True, 'source_sha256': self.hashes,
                       'rows': [{'id': p+str(i).zfill(2), 'passed': True, 'verified': ['tests'], 'missing': [], 'scope_denials': 0}
                                for p in ('I', 'R', 'E') for i in range(1, 7)]}
        self.put('level15-candidate.json', self.native)

    def put(self, name, data):
        (self.root/name).write_text(json.dumps(data))

    def test_exact_eighteen_missions_and_source_allowed(self):
        self.assertEqual(activation.verify(self.root, self.base)[1], self.hashes)

    def test_missing_failed_duplicate_unproven_or_out_of_scope_mission_denied(self):
        variants = [self.native['rows'][:17], [self.native['rows'][0]]*18,
                    [{**r, 'passed': False} for r in self.native['rows']],
                    [{**r, 'verified': []} for r in self.native['rows']],
                    [{**r, 'scope_denials': 1} for r in self.native['rows']]]
        for rows in variants:
            self.put('level15-candidate.json', {**self.native, 'rows': rows})
            with self.assertRaisesRegex(RuntimeError, 'candidate_canary_required'):
                activation.verify(self.root, self.base)

    def test_operator_file_appearing_at_prior_absent_path_is_not_overwritten(self):
        (self.base/'task-continuity.cjs').write_text('operator file')
        with self.assertRaisesRegex(RuntimeError, 'baseline_changed'):
            activation.verify(self.root, self.base)

    def test_candidate_edit_after_native_exam_is_rejected(self):
        (self.candidate/'task-continuity.cjs').write_text('later edit')
        with self.assertRaisesRegex(RuntimeError, 'candidate_changed'):
            activation.verify(self.root, self.base)

    def test_rollback_removes_only_new_owned_module_and_restores_existing_files(self):
        backup = self.root/'backup'
        backup.mkdir()
        for n in activation.FILES:
            if self.expected[n] is not None:
                (backup/n).write_text('old')
            (self.base/n).write_text('candidate')
        conflicts = activation.restore_owned(activation.FILES, self.hashes, backup, self.base,
                                            {n: 0o600 for n in activation.FILES},
                                            lambda source, target, mode: target.write_bytes(source.read_bytes()))
        self.assertEqual(conflicts, [])
        self.assertFalse((self.base/'task-continuity.cjs').exists())
        self.assertEqual((self.base/'agent-controller.cjs').read_text(), 'old')

    def test_untrusted_activation_helper_is_not_executed(self):
        (self.root/'scripts').mkdir()
        (self.root/'scripts/activate-debug-workflow.py').write_text('raise RuntimeError("untrusted")')
        with self.assertRaisesRegex(RuntimeError, 'activation_guards_changed'):
            activation.load_guards(self.root)

    def test_source_symlink_is_rejected(self):
        n = self.base/'task-continuity.cjs'
        n.symlink_to(self.base/'agent-controller.cjs')
        with self.assertRaisesRegex(RuntimeError, 'source_symlink_denied'):
            activation.verify(self.root, self.base)


if __name__ == '__main__':
    unittest.main()
