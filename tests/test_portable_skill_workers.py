"""Windows worker imports and browser-Node selection without network or limits."""
import importlib.util
import json
import os
from pathlib import Path
import signal
import sys
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

TOOLS = Path(__file__).resolve().parents[1] / 'runtime' / 'tools'
sys.path.insert(0, str(TOOLS))


def load(name):
    spec = importlib.util.spec_from_file_location('portable_' + name, TOOLS / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class PortableSkillWorkers(unittest.TestCase):
    def test_engineering_import_and_capabilities_without_resource(self):
        with patch.dict(sys.modules, {'resource': None}):
            worker = load('engineering_skill_worker')
        self.assertIsNone(worker.resource)
        result = worker.capabilities()
        self.assertEqual(len(result['operations']), 4)
        self.assertTrue(all(not item['code_execution'] for item in result['operations'].values()))

    def test_seo_import_and_limits_without_resource_or_sigxcpu(self):
        portable_signal = types.ModuleType('signal')
        portable_signal.__dict__.update({key: value for key, value in vars(signal).items() if key != 'SIGXCPU'})
        portable_signal.signal = Mock()
        with patch.dict(sys.modules, {'resource': None, 'signal': portable_signal}):
            worker = load('seo_skill_worker')
            self.assertIsNone(worker.resource)
            worker.configure_limits()
        portable_signal.signal.assert_called_once_with(signal.SIGTERM, worker.abort)
        with patch.object(worker, 'os', types.SimpleNamespace()), patch.object(worker, 'browser_capabilities', return_value=False), patch.object(worker, 'google_config', return_value=(None, 'google_credentials_not_configured')) as settings:
            result = worker.capabilities()
        settings.assert_called_once_with('local:windows')
        self.assertEqual(result['operations']['seo.visual']['status'], 'unavailable')
        self.assertEqual(result['operations']['seo.ga4']['reason'], 'google_credentials_not_configured')

    def test_windows_google_credentials_fail_closed_without_uid_acl_proof(self):
        extra = load('seo_extra')
        fake_os = types.SimpleNamespace(environ={'HARMONIKA_SEO_GOOGLE_CONFIG': str(TOOLS / 'fixture-do-not-read.json')})
        with patch.object(extra, 'os', fake_os):
            self.assertEqual(extra.google_settings('local:windows'), (None, 'google_config_permissions_unavailable'))

    def test_windows_directory_publication_uses_exclusive_native_rename(self):
        visual = load('seo_visual')
        with patch.object(visual.os, 'name', 'nt'), patch.object(visual.os, 'rename') as rename, patch.object(visual.ctypes, 'CDLL', side_effect=AssertionError('POSIX syscall')):
            visual.publish_directory('source', 'target')
            rename.assert_called_once_with('source', 'target')
            rename.side_effect = FileExistsError('existing destination')
            with self.assertRaises(FileExistsError):
                visual.publish_directory('source', 'target')

    def test_seo_browser_uses_browser_node_before_server_node(self):
        visual = load('seo_visual')
        reply = types.SimpleNamespace(returncode=0, stdout=b'{"available":true}')
        with patch.dict(os.environ, {'HARMONIKA_BROWSER_NODE': 'fixture-browser-node22', 'HARMONIKA_NODE': 'fixture-server-node18'}), patch.object(visual.subprocess, 'run', return_value=reply) as run:
            self.assertTrue(visual.browser_capabilities())
        self.assertEqual(run.call_args.args[0][0], 'fixture-browser-node22')

    def test_api_browser_uses_browser_node_before_server_node(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {'HARMONIKA_GLOBAL_CONFIG': tmp, 'HARMONIKA_BROWSER_NODE': sys.executable, 'HARMONIKA_NODE': 'fixture-server-node18'}):
            api = load('api_intel')
            reply = types.SimpleNamespace(returncode=0, stdout=b'{"ok":true}', stderr=b'')
            with patch.object(api.subprocess, 'run', return_value=reply) as run:
                api.discover({'url': 'https://example.invalid'})
            self.assertEqual(run.call_args.args[0][0], sys.executable)


if __name__ == '__main__':
    unittest.main()
