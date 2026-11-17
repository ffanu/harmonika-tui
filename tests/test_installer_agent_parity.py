"""Portable installer profile contract; fixtures never read the operator's home.

Run against another checkout during integration with HARMONIKA_TEST_SOURCE_DIR.
The fixture deliberately uses source files rather than a built release.
"""
import importlib.util
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch

SOURCE = Path(os.environ.get('HARMONIKA_TEST_SOURCE_DIR', Path(__file__).resolve().parents[1]))
SPEC = importlib.util.spec_from_file_location('installer_agent_parity_subject', SOURCE / 'runtime' / 'agent_features.py')
features = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(features)


class InstallerAgentParity(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='harmonika agent parity ')
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name) / 'synthetic home'
        self.home.mkdir()
        self.root = self.home / 'runtime install'
        self.config = self.home / '.config' / 'harmonika'
        self.tools = self.root / 'current' / 'tools'
        self.tools.mkdir(parents=True)
        self.config.mkdir(parents=True)
        self.python = Path(self.temp.name) / 'Python Runtime' / ('python.exe' if os.name == 'nt' else 'python3')
        self.node = Path(self.temp.name) / 'Node Runtime' / ('node.exe' if os.name == 'nt' else 'node')
        self.home_patch = patch.object(Path, 'home', return_value=self.home)
        self.home_patch.start()
        self.addCleanup(self.home_patch.stop)
        # Isolate ambient device/profile settings as well as filesystem access.
        self.env_patch = patch.dict(os.environ, {
            'HARMONIKA_GLOBAL_CONFIG': str(self.config),
            'HARMONIKA_CONFIG_DIR': str(self.config),
            'HARMONIKA_INSTALL_FEATURES': '1',
        })
        self.env_patch.start()
        self.addCleanup(self.env_patch.stop)
        self.manifest = {
            'schema_version': 1, 'profile': 'agent-parity-v1', 'agent_runtime': '3.2.1',
            'skills': ['harmonika-documents', 'harmonika-ssh', 'fixture-skill'],
            'plugins': ['tui-control', 'context-capacity'],
            'mcp': ['harmonika', 'harmonika_agent', 'harmonika_video'],
            'private_configuration_included': False,
        }
        self.write(self.tools / 'feature-manifest.json', self.manifest)
        for relative in ('mcp-python-entry.py', 'agent-v32/agent-v32.js',
                         'plugins/tui-control/package.json', 'plugins/context-capacity/package.json',
                         'video-control/native-video.cjs', 'AGENT-INSTRUCTIONS.md'):
            path = self.tools / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('{}' if path.suffix == '.json' else '# fixture\n', encoding='utf-8')
        (self.root / 'current' / 'skills').mkdir()

    def write(self, file, data):
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(json.dumps(data), encoding='utf-8')

    def read(self, file):
        return json.loads(file.read_text(encoding='utf-8'))

    def configure(self, config=None):
        return features.configure(self.root, config or self.config, self.python, self.node)

    def legacy_source(self, text):
        source = self.home / '.config' / 'harmonika-terminal' / 'mcp_server.py'
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text(text, encoding='utf-8')
        source.chmod(0o600)
        return source

    def symlink(self, link, target):
        try:
            link.symlink_to(target)
        except (NotImplementedError, OSError) as error:
            self.skipTest('Fixture filesystem does not permit symlinks: ' + type(error).__name__)

    def test_modern_skills_native_plugins_and_cli_python_are_registered(self):
        result = self.configure()
        self.assertTrue(result['configured'])
        native = self.read(self.config / 'opencode.json')
        cli = self.read(self.config / 'cli.json')
        self.assertEqual(native['skills'], [str(self.root / 'current' / 'skills')])
        self.assertEqual(native['plugins'], [
            {'package': str(self.tools / 'plugins' / 'tui-control')},
            {'package': str(self.tools / 'plugins' / 'context-capacity')},
        ])
        self.assertEqual(cli['plugins'], [{'package': str(self.tools / 'plugins' / 'tui-control'), 'options': {'python': str(self.python)}}])
        self.assertEqual(cli['tabs'], {'mode': 'off'})
        self.assertEqual(native['mcp']['harmonika_agent']['command'], [str(self.python), '-I', str(self.tools / 'mcp-python-entry.py')])
        self.assertEqual(native['mcp']['harmonika_video']['command'], [str(self.node), str(self.tools / 'video-control' / 'native-video.cjs')])
        self.assertEqual(result['skills'], 3)
        self.assertFalse((self.config / 'license.json').exists())

    def test_custom_mcp_plugins_tabs_instructions_and_identity_survive(self):
        custom = {'package': 'operator-plugin', 'options': {'keep': True}}
        remote = {'type': 'remote', 'url': 'https://example.invalid/mcp', 'enabled': False}
        self.write(self.config / 'opencode.json', {
            'skills': ['operator-skills'], 'plugins': [custom, 'another-plugin', r'C:\old\control-releases\2026.11.12.4'],
            'mcp': {'operator': remote, 'harmonika': {'type': 'local', 'command': ['existing-core'], 'enabled': True}},
            'instructions': ['operator-rules.md'], 'model': 'harmonika/operator-choice',
        })
        self.write(self.config / 'cli.json', {'plugins': [custom], 'tabs': {'mode': 'auto'}, 'theme': 'operator-theme'})
        identity = self.config / 'device-id.json'
        identity.write_bytes(b'{"device_id":"synthetic-device"}\n')
        session = self.config / 'session.json'
        session.write_bytes(b'{"fixture_session":"unchanged"}\n')
        before = (identity.read_bytes(), session.read_bytes())
        self.configure()
        native, cli = self.read(self.config / 'opencode.json'), self.read(self.config / 'cli.json')
        self.assertEqual(native['plugins'][:2], [custom, 'another-plugin'])
        self.assertEqual(len(native['plugins']), 4)
        self.assertEqual(native['mcp']['operator'], remote)
        self.assertEqual(native['mcp']['harmonika']['command'], ['existing-core'])
        self.assertEqual(native['instructions'][0], 'operator-rules.md')
        self.assertIn(str(self.tools / 'AGENT-INSTRUCTIONS.md'), native['instructions'])
        self.assertEqual(native['model'], 'harmonika/operator-choice')
        self.assertEqual(cli['tabs'], {'mode': 'auto'})
        self.assertEqual(cli['theme'], 'operator-theme')
        self.assertEqual(cli['plugins'][0], custom)
        self.assertEqual(before, (identity.read_bytes(), session.read_bytes()))

    def test_skills_paths_and_urls_legacy_shape_survives_and_is_idempotent(self):
        self.write(self.config / 'opencode.json', {'skills': {'paths': ['custom-skills'], 'urls': ['https://example.invalid/skills']}})
        self.configure()
        first = {name: (self.config / name).read_bytes() for name in ('opencode.json', 'cli.json')}
        self.configure()
        self.assertEqual(first, {name: (self.config / name).read_bytes() for name in first})
        self.assertEqual(self.read(self.config / 'opencode.json')['skills'], ['custom-skills', 'https://example.invalid/skills', str(self.root / 'current' / 'skills')])

    def test_configuration_file_symlink_is_rejected_without_touching_destination(self):
        outside = self.home / 'operator.json'
        outside.write_bytes(b'{"operator":"preserve"}')
        self.symlink(self.config / 'opencode.json', outside)
        with self.assertRaisesRegex(ValueError, 'unsafe_feature_configuration'):
            self.configure()
        self.assertEqual(outside.read_bytes(), b'{"operator":"preserve"}')
        self.assertTrue((self.config / 'opencode.json').is_symlink())

    def test_cli_symlink_is_rejected_before_writing_native_configuration(self):
        outside = self.home / 'operator-cli.json'
        outside.write_bytes(b'{"tabs":{"mode":"auto"}}')
        self.symlink(self.config / 'cli.json', outside)
        with self.assertRaisesRegex(ValueError, 'unsafe_feature_configuration'):
            self.configure()
        self.assertEqual(outside.read_bytes(), b'{"tabs":{"mode":"auto"}}')
        self.assertFalse((self.config / 'opencode.json').exists())

    def test_connector_migration_uses_ast_only_and_preserves_private_existing_values(self):
        sentinel = self.home / 'MUST_NOT_EXECUTE'
        self.legacy_source(
            'from pathlib import Path\n'
            'Path(' + repr(str(sentinel)) + ').touch()\n'
            'VISION_TOKEN = "fixture-vision-token-123456"\n'
            'MEDIA_TOKEN = os.environ.get("HARMONIKA_MEDIA_TOKEN", "fixture-media-token-123456")\n'
            'STUDIO_KEY = "fixture-studio-token-123456"\n'
            'UNRELATED_TOKEN = "fixture-do-not-migrate-123456"\n')
        target = self.config / 'private-connectors.json'
        self.write(target, {'HARMONIKA_MEDIA_TOKEN': 'fixture-existing-operator-token'})
        self.assertEqual(features.migrate_local_connectors(self.config), 2)
        data = self.read(target)
        self.assertEqual(data, {
            'HARMONIKA_VISION_TOKEN': 'fixture-vision-token-123456',
            'HARMONIKA_MEDIA_TOKEN': 'fixture-existing-operator-token',
            'HARMONIKA_STUDIO_KEY': 'fixture-studio-token-123456',
        })
        self.assertFalse(sentinel.exists())
        self.assertEqual(features.migrate_local_connectors(self.config), 0)
        if os.name != 'nt':
            self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o600)
        self.assertNotIn('fixture-vision-token', (self.tools / 'feature-manifest.json').read_text())

    def test_connector_migration_refuses_symlink_source_and_destination(self):
        source = self.legacy_source('VISION_TOKEN = "fixture-vision-token-123456"\n')
        external = self.home / 'external-source.py'
        source.replace(external)
        self.symlink(source, external)
        self.assertEqual(features.migrate_local_connectors(self.config), 0)
        self.assertFalse((self.config / 'private-connectors.json').exists())
        source.unlink()
        external.replace(source)
        private = self.home / 'external-private.json'
        private.write_bytes(b'{"operator":"preserve"}')
        self.symlink(self.config / 'private-connectors.json', private)
        with self.assertRaisesRegex(ValueError, 'unsafe_feature_configuration'):
            features.migrate_local_connectors(self.config)
        self.assertEqual(private.read_bytes(), b'{"operator":"preserve"}')

    def test_isolated_profile_does_not_copy_local_owner_connectors(self):
        self.legacy_source('VISION_TOKEN = "fixture-vision-token-123456"\n')
        isolated = self.home / 'isolated test profile'
        isolated.mkdir()
        self.configure(isolated)
        self.assertFalse((isolated / 'private-connectors.json').exists())

    def test_manifest_install_and_dependency_readiness_do_not_claim_service_activation(self):
        installed = features.status(self.root, self.config)
        self.assertTrue(installed['installed'])
        self.assertFalse(installed['skills_registered'])
        self.assertFalse(installed['native_control_registered'])
        self.assertEqual(installed['remote_services'], 'readiness_and_account_access_required')
        self.assertFalse(any(installed['mcp_servers_registered'].values()))
        self.write(self.root / 'feature-setup-status.json', {'status': 'partial', 'browser': {'ready': False}, 'documents': {'ready': True}})
        self.configure()
        configured = features.status(self.root, self.config)
        self.assertTrue(configured['skills_registered'])
        self.assertTrue(configured['native_control_registered'])
        self.assertTrue(configured['verified_agent_entrypoint'])
        self.assertFalse(configured['mcp_servers_registered']['harmonika'])
        self.assertTrue(configured['mcp_servers_registered']['harmonika_agent'])
        self.assertEqual(configured['dependencies']['status'], 'partial')
        self.assertEqual(configured['remote_services'], 'readiness_and_account_access_required')
        self.assertFalse((self.config / 'license.json').exists())
        (self.tools / 'agent-v32' / 'agent-v32.js').unlink()
        self.assertFalse(features.status(self.root, self.config)['verified_agent_entrypoint'])

    def test_missing_manifest_is_legacy_and_wrong_schema_fails_closed(self):
        manifest = self.tools / 'feature-manifest.json'
        manifest.unlink()
        self.assertEqual(self.configure(), {'configured': False, 'reason': 'legacy_runtime'})
        self.assertEqual(features.status(self.root, self.config)['status'], 'legacy_runtime')
        self.write(manifest, {'schema_version': 999})
        with self.assertRaisesRegex(ValueError, 'invalid_feature_manifest'):
            self.configure()
        self.assertFalse((self.config / 'opencode.json').exists())

    def test_setup_respects_explicit_operator_opt_out_without_loading_helper(self):
        # Loading this helper would fail immediately; operator choice must win.
        (self.tools / 'feature_setup.py').write_text('raise AssertionError("helper loaded")\n', encoding='utf-8')
        with patch.dict(os.environ, {'HARMONIKA_INSTALL_FEATURES': '0'}):
            self.assertEqual(features.setup(self.root), {'status': 'core_only', 'reason': 'explicit_operator_choice'})


if __name__ == '__main__':
    unittest.main()
