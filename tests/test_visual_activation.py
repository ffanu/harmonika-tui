import hashlib
import pathlib
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]


class Activation(unittest.TestCase):
    def test_refresh_existing_delegate_without_rewriting_private_mcp(self):
        with tempfile.TemporaryDirectory() as directory:
            owner = pathlib.Path(directory)
            original = b'def tool_analyze_visual_image(args):\n    from visual_media import analyze\n    return analyze(args, VISION_TOKEN)\n'
            (owner / 'mcp_server.py').write_bytes(original)
            (owner / 'visual_media.py').write_text('# previous helper\n')
            command = [sys.executable, str(ROOT / 'scripts/activate-visual-quality.py'), '--owner', directory]
            first = subprocess.run(command, capture_output=True, text=True, check=True)
            self.assertIn("'updated_files': 2", first.stdout)
            self.assertEqual((owner / 'mcp_server.py').read_bytes(), original)
            for name in ('visual_media.py', 'media_tunnels.py'):
                self.assertEqual((owner / name).read_bytes(), (ROOT / 'runtime/tools' / name).read_bytes())
                self.assertEqual((owner / name).stat().st_mode & 0o777, 0o600)
            backup = list((owner / 'visual-quality-backups').iterdir())
            self.assertEqual(len(backup), 1)
            self.assertEqual((backup[0] / 'visual_media.py').read_text(), '# previous helper\n')
            second = subprocess.run(command, capture_output=True, text=True, check=True)
            self.assertIn("'updated_files': 0", second.stdout)
            self.assertEqual(len(list((owner / 'visual-quality-backups').iterdir())), 1)


if __name__ == '__main__':
    unittest.main()
