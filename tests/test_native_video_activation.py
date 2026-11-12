"""Owner config activation must preserve existing integrations and user settings."""
import json
import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/activate-native-video.py'


@unittest.skipUnless(shutil.which('node'), 'activation requires existing Node runtime')
class NativeVideoActivationTest(unittest.TestCase):
    def fixture(self, home):
        root = home / '.config/harmonika-terminal'; root.mkdir(parents=True)
        adapter = root / 'current/tools/video-mcp.cjs'; adapter.parent.mkdir(parents=True)
        adapter.write_text('// inert test adapter; never executed\n')
        before = {'model': 'owner/unchanged', 'permissions': {'bash': 'deny'},
                  'plugins': ['existing-plugin'], 'mcp': {'existing': {'enabled': True}},
                  'skills': ['existing-skills'], 'instructions': ['existing.md']}
        config = root / 'harmonika.json'; config.write_text(json.dumps(before))
        return root, config, before

    def activate(self, home, *args):
        return subprocess.run([sys.executable, str(SCRIPT), *map(str,args)], env={**os.environ, 'HOME': str(home)},
                              capture_output=True, text=True, timeout=15)

    def package(self, home):
        spec = importlib.util.spec_from_file_location('fixture_release', SCRIPT.parents[1]/'runtime/tools/tui_release.py')
        release = importlib.util.module_from_spec(spec); spec.loader.exec_module(release)
        private = home/'fixture-private.pem'; public = home/'fixture-public.pem'
        subprocess.run(['node', '-e', "const c=require('crypto'),f=require('fs');const k=c.generateKeyPairSync('ed25519');f.writeFileSync(process.argv[1],k.privateKey.export({type:'pkcs8',format:'pem'}));f.writeFileSync(process.argv[2],k.publicKey.export({type:'spki',format:'pem'}));", str(private), str(public)], check=True)
        private.chmod(0o600)
        source = home/'fixture-source'; source.mkdir(); (source/'native-video.cjs').write_text('// fixture\n')
        package = home/'fixture.zip'
        release.build_release(source, package, '2026.11.13.1', private)
        return package, public

    def test_existing_settings_preserved_and_rerun_idempotent(self):
        with tempfile.TemporaryDirectory() as folder:
            home = Path(folder); root, config, before = self.fixture(home)
            self.assertEqual(self.activate(home).returncode, 0)
            after = json.loads(config.read_text())
            for key in ('model', 'permissions', 'plugins'):
                self.assertEqual(after[key], before[key])
            self.assertEqual(after['mcp']['existing'], before['mcp']['existing'])
            self.assertEqual(after['skills'][0], 'existing-skills')
            self.assertEqual(after['instructions'][0], 'existing.md')
            self.assertEqual(config.stat().st_mode & 0o777, 0o600)
            self.assertEqual(self.activate(home).returncode, 0)
            self.assertEqual(json.loads(config.read_text()), after)
            backups = list((root / 'video-recovery-backups').glob('*/harmonika.json'))
            self.assertTrue(any(json.loads(p.read_text()) == before for p in backups))

    def test_conflicting_video_config_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as folder:
            home = Path(folder); _, config, before = self.fixture(home)
            before['mcp']['harmonika_video'] = {'enabled': False, 'command': ['owner-choice']}
            original = json.dumps(before); config.write_text(original)
            self.assertNotEqual(self.activate(home).returncode, 0)
            self.assertEqual(config.read_text(), original)

    def test_signed_upgrade_preserves_owner_config_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as folder:
            home = Path(folder); root, config, before = self.fixture(home)
            self.assertEqual(self.activate(home).returncode, 0)
            package, public = self.package(home)
            result = self.activate(home, '--package', package, '--public-key', public)
            self.assertEqual(result.returncode, 0, result.stderr)
            after = json.loads(config.read_text())
            self.assertEqual(after['model'], before['model'])
            self.assertEqual(after['permissions'], before['permissions'])
            self.assertEqual(after['mcp']['existing'], before['mcp']['existing'])
            self.assertTrue(after['mcp']['harmonika_video']['command'][-1].endswith('/native-video.cjs'))
            self.assertTrue(json.loads(result.stdout)['signed_package'])
            self.assertEqual(self.activate(home, '--package', package, '--public-key', public).returncode, 0)

    def test_release_parent_symlink_rejected_before_owner_config_write(self):
        with tempfile.TemporaryDirectory() as folder:
            home = Path(folder); root, config, _ = self.fixture(home)
            package, public = self.package(home)
            outside = home/'outside'; outside.mkdir(); (root/'native-video-releases').symlink_to(outside)
            before = config.read_bytes()
            self.assertNotEqual(self.activate(home, '--package', package, '--public-key', public).returncode, 0)
            self.assertEqual(config.read_bytes(), before)
            self.assertFalse(list(outside.iterdir()))

    def test_signed_previous_companion_can_upgrade_without_overwriting_owner_choice(self):
        with tempfile.TemporaryDirectory() as folder:
            home = Path(folder); _, config, before = self.fixture(home)
            package, public = self.package(home)
            self.assertEqual(self.activate(home, '--package', package, '--public-key', public).returncode, 0)
            spec = importlib.util.spec_from_file_location('upgrade_release', SCRIPT.parents[1]/'runtime/tools/tui_release.py')
            release = importlib.util.module_from_spec(spec); spec.loader.exec_module(release)
            source = home/'fixture-source'; (source/'native-video.cjs').write_text('// updated fixture\n')
            updated = home/'updated.zip'; release.build_release(source, updated, '2026.11.13.2', home/'fixture-private.pem')
            self.assertNotEqual(self.activate(home, '--package', updated, '--public-key', public).returncode, 0)
            result = self.activate(home, '--package', updated, '--public-key', public, '--previous-package', package)
            self.assertEqual(result.returncode, 0, result.stderr)
            value = json.loads(config.read_text())
            self.assertIn('/2026.11.13.2/native-video.cjs',value['mcp']['harmonika_video']['command'][-1])
            self.assertEqual(value['permissions'], before['permissions'])


if __name__ == '__main__':
    unittest.main()
