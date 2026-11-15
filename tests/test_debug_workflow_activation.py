"""Deployment guards use synthetic processes/files; never contact systemd."""
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location(
    'debug_activation', Path(__file__).resolve().parents[1]/'scripts/activate-debug-workflow.py')
activation = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(activation)


class ActivationGuards(unittest.TestCase):
    def state(self, main='10', connections='', child=False, rollback=False):
        def output(*args):
            if args[0] == 'ss':
                return connections
            return '20 '+main+' worker' if child else '20 19 unrelated'
        with patch.object(activation, 'pid', return_value=main), \
                patch.object(activation, 'run', side_effect=output):
            return activation.idle(rollback)

    def test_idle_gateway_is_eligible(self):
        self.assertTrue(self.state())

    def test_connections_prevent_restart_even_during_rollback(self):
        self.assertFalse(self.state(connections='synthetic socket'))
        self.assertFalse(self.state(main='0', connections='synthetic socket', rollback=True))

    def test_active_model_child_prevents_restart(self):
        self.assertFalse(self.state(child=True))

    def test_stopped_gateway_only_eligible_for_rollback(self):
        self.assertFalse(self.state(main='0'))
        self.assertTrue(self.state(main='0', rollback=True))

    def test_two_idle_observations_required(self):
        with patch.object(activation, 'idle', side_effect=[True, False, True, True]) as idle, \
                patch.object(activation.time, 'sleep'):
            activation.await_idle()
            self.assertEqual(idle.call_count, 4)

    def test_atomic_copy_preserves_contents_and_mode(self):
        with tempfile.TemporaryDirectory() as directory:
            source, target = Path(directory)/'source', Path(directory)/'target'
            source.write_bytes(b'candidate\n')
            target.write_bytes(b'original\n')
            activation.replace(source, target, 0o600)
            self.assertEqual(target.read_bytes(), b'candidate\n')
            self.assertEqual(target.stat().st_mode & 0o777, 0o600)
            self.assertFalse(target.with_name(target.name+'.debug-activation.tmp').exists())

    def test_failed_copy_keeps_original_and_cleans_temporary(self):
        with tempfile.TemporaryDirectory() as directory:
            source, target = Path(directory)/'missing', Path(directory)/'target'
            target.write_bytes(b'original\n')
            with self.assertRaises(FileNotFoundError):
                activation.replace(source, target, 0o600)
            self.assertEqual(target.read_bytes(), b'original\n')
            self.assertFalse(target.with_name(target.name+'.debug-activation.tmp').exists())


if __name__ == '__main__':
    unittest.main()
