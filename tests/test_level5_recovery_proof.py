import hashlib
import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('level5_recovery', Path(__file__).resolve().parents[1] / 'scripts/level5-lab/recovery_proof.py')
proof = importlib.util.module_from_spec(spec)
spec.loader.exec_module(proof)
KEY = hashlib.sha256(b'synthetic-order').hexdigest()
OTHER = hashlib.sha256(b'other-order').hexdigest()


class RecoveryProof(unittest.TestCase):
    def drop(self):
        state = {}
        proof.track(state, 'agent', 'POST', '/api/tickets', 201, KEY, True)
        return state

    def get(self, state, key=KEY, status=200, origin='agent'):
        proof.track(state, origin, 'GET', '/api/requests/:key', status, key, False)

    def test_requires_actual_drop(self):
        self.assertFalse(proof.is_reconciled({}))

    def test_exact_key_reconciles(self):
        state = self.drop()
        self.get(state)
        self.assertTrue(proof.is_reconciled(state))

    def test_other_key_cannot_satisfy(self):
        state = self.drop()
        self.get(state, OTHER)
        self.assertFalse(proof.is_reconciled(state))

    def test_examiner_cannot_satisfy_agent(self):
        state = self.drop()
        self.get(state, origin='examiner')
        self.assertFalse(proof.is_reconciled(state))

    def test_post_before_reconcile_stays_failed(self):
        state = self.drop()
        proof.track(state, 'agent', 'POST', '/api/tickets', 200, KEY, False)
        self.get(state)
        self.assertFalse(proof.is_reconciled(state))

    def test_bounded_delayed_visibility(self):
        state = self.drop()
        self.get(state, status=404)
        self.get(state)
        self.assertTrue(proof.is_reconciled(state))

    def test_four_checks_fail_even_if_last_succeeds(self):
        state = self.drop()
        for _ in range(3):
            self.get(state, status=404)
        self.get(state)
        self.assertFalse(proof.is_reconciled(state))

    def test_overflow_is_bounded_and_not_a_pass(self):
        state = {}
        for _ in range(33):
            proof.track(state, 'agent', 'POST', '/api/tickets', 201, KEY, True)
            self.get(state)
        self.assertEqual(len(state['lost_ack_incidents']), 32)
        self.assertFalse(proof.is_reconciled(state))


if __name__ == '__main__':
    unittest.main()
