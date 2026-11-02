"""Tests for engine/render-config.py: placeholder rendering and honest adaptivity."""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RENDER = ROOT / 'engine' / 'render-config.py'
TEMPLATE = ROOT / 'engine' / 'config' / 'harmonika.json'


def render(home, mcp_server=''):
    out = Path(tempfile.mkdtemp()) / 'harmonika.json'
    subprocess.run([sys.executable, str(RENDER), '--home', home, '--python', sys.executable,
                    '--mcp-server', mcp_server, '--template', str(TEMPLATE), '--out', str(out)],
                   check=True, capture_output=True, text=True)
    return json.loads(out.read_text())


class RenderTests(unittest.TestCase):
    def test_api_key_is_never_literal(self):
        config = render('/tmp/example-home')
        self.assertEqual(config['provider']['harmonika']['options']['apiKey'], '{env:HARMONIKA_API_KEY}')

    def test_home_placeholder_is_substituted(self):
        config = render('/tmp/example-home')
        self.assertIn('/tmp/example-home/.config/harmonika/HARMONIKA.md', config['instructions'])
        self.assertNotIn('@HOME@', json.dumps(config))

    def test_missing_mcp_server_drops_the_block(self):
        self.assertNotIn('mcp', render('/tmp/example-home', '/nonexistent/mcp_server.py'))

    def test_upstream_provider_is_disabled(self):
        self.assertEqual(render('/tmp/example-home')['disabled_providers'], ['opencode'])

    def test_share_disabled_and_snapshots_on(self):
        config = render('/tmp/example-home')
        self.assertEqual(config['share'], 'disabled')
        self.assertIs(config['snapshots'], True)

    def test_lsp_entries_without_binaries_are_dropped(self):
        config = render('/tmp/example-home')
        for name in ('typescript', 'python', 'go', 'html', 'css', 'json'):
            command = (config.get('lsp') or {}).get(name, {}).get('command', [None])[0]
            if command is None:
                continue
            import shutil
            self.assertIsNotNone(shutil.which(command), f'{name} dipertahankan walau {command} tidak ada')


if __name__ == '__main__':
    unittest.main()
