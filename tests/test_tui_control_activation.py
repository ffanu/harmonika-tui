"""Real signed package/config roundtrips in a synthetic owner directory."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('control_activation', ROOT/'scripts/activate-tui-control.py')
activation = importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(activation)


class ActivationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='synthetic-control-install-')
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.root = self.base/'owner';self.root.mkdir(mode=0o700)
        self.binary = self.base/'native';self.binary.write_bytes(b'SYNTHETIC-NATIVE-UNCHANGED')
        self.original = {'provider': {'synthetic': {'options': {'apiKey': '{env:TEST_ONLY}'}}}, 'model': 'synthetic/model', 'permission': {'bash': 'ask'}, 'mcp': {'fixture': {'enabled': True}}}
        self.write('harmonika.json', self.original)
        self.write('cli.json', {'plugins': ['synthetic-existing-plugin'], 'theme': 'existing-theme'})
        self.source = self.base/'source';self.source.mkdir()
        for name in activation.REQUIRED:
            path = self.source/name;path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('{}' if path.suffix == '.json' else '# synthetic fixture\n')
        self.private = self.base/'private.pem';self.public = self.base/'public.pem'
        subprocess.run([activation.release._node_command(), '-e', "const fs=require('fs'),c=require('crypto'),k=c.generateKeyPairSync('ed25519');fs.writeFileSync(process.argv[1],k.privateKey.export({type:'pkcs8',format:'pem'}));fs.writeFileSync(process.argv[2],k.publicKey.export({type:'spki',format:'pem'}));", str(self.private), str(self.public)], check=True, capture_output=True)
        self.package = self.base/'plugin.zip'
        self.metadata = activation.release.build_release(self.source, self.package, '2026.11.12.2', self.private, list(activation.REQUIRED))
        self.smoke = {'ok': True, 'version': '2026.11.12.2', 'native_sha256': activation.sha(self.binary), 'package_sha256': activation.sha(self.package)}

    def write(self, name, value):
        path = self.root/name;path.write_text(json.dumps(value));path.chmod(0o600)

    def activate(self, smoke=None):
        return activation.activate(self.package, self.public, self.root, self.binary, smoke or self.smoke)

    def test_activation_preserves_settings_and_exact_rollback(self):
        original = {name: (self.root/name).read_bytes() for name in ('harmonika.json','cli.json')}
        with mock.patch('os.kill', side_effect=AssertionError('no process signals')):
            result = self.activate()
            self.assertTrue(result['ok']);self.assertFalse(result['automatic_update'])
            config = json.loads((self.root/'harmonika.json').read_text())
            self.assertEqual({key: config[key] for key in self.original}, self.original)
            self.assertEqual(config['update'], 'disable')
            self.assertTrue(Path(config['plugins'][-1]['package'],'runtime.mjs').is_file())
            cli = json.loads((self.root/'cli.json').read_text())
            self.assertEqual(cli['theme'], 'existing-theme');self.assertEqual(cli['plugins'][0], 'synthetic-existing-plugin')
            self.assertTrue(Path(cli['plugins'][-1]['package'],'tui.tsx').is_file())
            self.assertEqual((self.root/'cli.json').stat().st_mode & 0o777, 0o600)
            activation.rollback(self.root, self.binary, result['backup_id'])
        for name, data in original.items():self.assertEqual((self.root/name).read_bytes(), data)
        self.assertFalse((self.root/'control-active.json').exists())
        self.assertEqual(self.binary.read_bytes(), b'SYNTHETIC-NATIVE-UNCHANGED')

    def test_absent_cli_is_restored_as_absent(self):
        (self.root/'cli.json').unlink()
        result = self.activate();activation.rollback(self.root, self.binary, result['backup_id'])
        self.assertFalse((self.root/'cli.json').exists())

    def test_stale_or_wrong_package_smoke_refused(self):
        for field in ('ok','version','native_sha256','package_sha256'):
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, 'matching_native_smoke_required'):
                self.activate({**self.smoke, field: False if field=='ok' else 'wrong'})
        self.assertFalse((self.root/'control-active.json').exists())

    def test_tampered_package_refused_before_configs_change(self):
        data = self.package.read_bytes();self.package.write_bytes(data[:60]+b'changed'+data[67:])
        with self.assertRaises(Exception):self.activate()
        self.assertEqual(json.loads((self.root/'harmonika.json').read_text()), self.original)

    def test_rollback_refuses_overwriting_later_user_change(self):
        result = self.activate();self.write('cli.json', {'theme':'later-user-change'})
        with self.assertRaisesRegex(ValueError, 'configuration_changed_since_activation'):
            activation.rollback(self.root, self.binary, result['backup_id'])
        self.assertEqual(json.loads((self.root/'cli.json').read_text())['theme'], 'later-user-change')

    def test_server_runtime_plugins_preserved_and_not_duplicated(self):
        self.write('harmonika.json', {**self.original, 'plugin': ['legacy-fixture'], 'plugins': [{'package':'existing-runtime'}]})
        first = self.activate()
        self.activate()
        config = json.loads((self.root/'harmonika.json').read_text())
        self.assertEqual(config['plugin'], ['legacy-fixture'])
        self.assertEqual(config['plugins'][0], {'package':'existing-runtime'})
        self.assertEqual(len(config['plugins']), 2)
        self.assertTrue(first['runtime_guard_configured'])

    def test_reactivation_does_not_duplicate_plugin_and_detects_changed_files(self):
        self.activate();self.activate()
        cli = json.loads((self.root/'cli.json').read_text());self.assertEqual(len(cli['plugins']), 2)
        Path(cli['plugins'][-1]['package'], 'model.mjs').write_text('changed')
        with self.assertRaisesRegex(ValueError, 'installed_control_file_changed'):self.activate()

    def test_failed_atomic_activation_restores_only_own_writes(self):
        original = activation.atomic
        def fail(path, data):
            if Path(path) == self.root/'cli.json':raise OSError('synthetic disk failure')
            return original(path, data)
        with mock.patch.object(activation, 'atomic', side_effect=fail), self.assertRaises(OSError):self.activate()
        self.assertEqual(json.loads((self.root/'harmonika.json').read_text()), self.original)
        self.assertFalse((self.root/'control-active.json').exists())

    def test_legacy_cli_requires_explicit_migration(self):
        (self.root/'cli.json').unlink();self.write('tui.json', {'theme':'synthetic-old-theme'})
        with self.assertRaisesRegex(ValueError, 'legacy_cli_migration_required'):self.activate()


if __name__ == '__main__':unittest.main()
