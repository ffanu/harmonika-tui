"""Behavioral checks for the bounded terminal QA reference harness."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / 'skills/harmonika-terminal-qa/scripts/terminal_qa.py'
spec = importlib.util.spec_from_file_location('harmonika_terminal_qa', SCRIPT)
qa = importlib.util.module_from_spec(spec)
spec.loader.exec_module(qa)


class TerminalQATests(unittest.TestCase):
    def test_cancel_retry_rejects_old_request(self):
        model = qa.Model()
        model.event('type', 'one')
        model.event('submit')
        old = model.active
        model.event('cancel')
        model.event('type', 'two')
        model.event('submit')
        current = model.active
        for kind in ('delta', 'done', 'error'):
            model.event(kind, 'obsolete', old)
        self.assertEqual(model.active, current)
        self.assertEqual(model.reply, '')
        model.event('delta', 'fresh', current)
        self.assertEqual(model.reply, 'fresh')

    def test_focus_preserves_draft_and_blocks_submit(self):
        model = qa.Model()
        model.event('type', 'draft')
        model.event('tab')
        model.event('type', 'wrong')
        model.event('submit')
        self.assertEqual(model.draft, 'draft')
        self.assertIsNone(model.active)
        self.assertEqual(model.snapshot(1, 1)['focus'], 'history')

    def test_clipping_preserves_clusters_and_resets_color(self):
        self.assertEqual(qa.plain(qa.clip('\x1b[31m界e\u0301🙂', 3)), '界e\u0301')
        self.assertEqual(qa.plain(qa.clip('👩\u200d💻!', 2)), '👩\u200d💻')
        self.assertEqual(qa.plain(qa.clip('界', 1)), '')
        self.assertTrue(qa.clip('\x1b[31mab', 1).endswith('\x1b[0m'))
        self.assertNotIn('\x1b[2J', qa.clip('\x1b[2Jabc', 2))
        self.assertNotIn('secret', qa.clip('\x1b]0;secret\x07abc', 3))

    def test_tiny_layout_bounded_without_destroying_state(self):
        model = qa.Model()
        model.event('type', '界🙂' * 80)
        before = model.draft
        for rows, cols in ((1, 1), (2, 7), (24, 80), (3, 2)):
            snapshot = model.snapshot(rows, cols)
            self.assertLessEqual(len(snapshot['lines']), rows)
            self.assertTrue(all(qa.cell_width(line) <= cols for line in snapshot['lines']))
            self.assertEqual(snapshot['draft'], before)

    def test_cli_self_test_and_default_are_bounded_json(self):
        for args in ([], ['self-test']):
            result = subprocess.run([sys.executable, '-I', str(SCRIPT)] + args,
                                    capture_output=True, text=True, timeout=15)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertLess(len(result.stdout), 4096)
            value = json.loads(result.stdout)
            self.assertTrue(value['ok'])
            self.assertFalse(value['network'])
            self.assertEqual(value['reducer'], 'passed')
            self.assertEqual(value['layout'], 'passed')
            self.assertIn(value['pty']['status'], ('passed', 'skipped'))

    def test_unknown_command_rejected(self):
        result = subprocess.run([sys.executable, str(SCRIPT), 'arbitrary-command'],
                                capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 2)
        self.assertIn('invalid choice', result.stderr)


if __name__ == '__main__':
    unittest.main()
