import importlib.util
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('v32_install',ROOT/'scripts/activate-agent-v32.py')
installer=importlib.util.module_from_spec(spec);spec.loader.exec_module(installer)

@unittest.skipUnless(shutil.which('node'),'Node required')
class Activation(unittest.TestCase):
    def test_explicit_entrypoint_preserves_native_launcher(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);launcher=root/'harmonika'
            original=b'#!/bin/sh\nexec /bin/echo NATIVE "$@"\n'
            launcher.write_bytes(original);launcher.chmod(0o755)
            record=installer.activate(root,ROOT/'tools',shutil.which('node'))
            self.assertTrue(record['active']);self.assertFalse(record['native_binary_changed'])
            result=subprocess.run([str(launcher),'--version'],capture_output=True,text=True)
            self.assertEqual(result.stdout.strip(),'NATIVE --version')
            self.assertEqual((Path(record['backup'])/'launcher').read_bytes(),original)
            self.assertEqual((Path(record['backup'])/'launcher').stat().st_mode&0o777,0o600)
            with self.assertRaisesRegex(RuntimeError,'already_installed'):
                installer.activate(root,ROOT/'tools',shutil.which('node'))

    def test_invalid_launcher_never_replaced(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);launcher=root/'harmonika';launcher.write_bytes(b'not a shell launcher')
            with self.assertRaisesRegex(RuntimeError,'unsupported_launcher'):
                installer.activate(root,ROOT/'tools',shutil.which('node'))
            self.assertEqual(launcher.read_bytes(),b'not a shell launcher')

    def test_upgrade_compares_active_version_and_preserves_launcher(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);launcher=root/'harmonika';launcher.write_bytes(b'#!/bin/sh\nexec /bin/echo NATIVE "$@"\n');launcher.chmod(0o755)
            first=installer.activate(root,ROOT/'tools',shutil.which('node'));before=launcher.read_bytes()
            candidate=root/'candidate';candidate.mkdir()
            for name in installer.FILES:shutil.copy2(ROOT/'tools'/name,candidate/name)
            with (candidate/'agent-v32.js').open('a') as f:f.write('\n// synthetic upgrade fixture\n')
            with self.assertRaisesRegex(RuntimeError,'active_version_changed'):
                installer.upgrade(root,candidate,shutil.which('node'),'wrong-version')
            second=installer.upgrade(root,candidate,shutil.which('node'),first['version'])
            self.assertTrue(second['active']);self.assertEqual(second['previous_version'],first['version'])
            self.assertNotEqual(second['version'],first['version']);self.assertEqual(launcher.read_bytes(),before)
            self.assertTrue((root/'agent-v32'/first['version']).is_dir())

    def test_exact_manifest_preserves_observed_operator_update(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);launcher=root/'harmonika';launcher.write_bytes(b'#!/bin/sh\nexec /bin/echo NATIVE "$@"\n');launcher.chmod(0o755)
            first=installer.activate(root,ROOT/'tools',shutil.which('node'))
            active=(root/'agent-v32/current').resolve()
            with (active/'agent-v32.js').open('a') as f:f.write('\n// operator update\n')
            observed={n:installer.sha((active/n).read_bytes()) for n in installer.FILES}
            candidate=root/'candidate';candidate.mkdir()
            for name in installer.FILES:shutil.copy2(ROOT/'tools'/name,candidate/name)
            with (candidate/'agent-v32.js').open('a') as f:f.write('\n// reviewed fix\n')
            with self.assertRaisesRegex(RuntimeError,'active_bundle_changed'):
                installer.upgrade(root,candidate,shutil.which('node'),first['version'])
            wrong=dict(observed);wrong['agent-v32.js']='0'*64
            with self.assertRaisesRegex(RuntimeError,'active_bundle_changed'):
                installer.upgrade(root,candidate,shutil.which('node'),first['version'],wrong)
            before=launcher.read_bytes()
            record=installer.upgrade(root,candidate,shutil.which('node'),first['version'],observed)
            self.assertEqual(record['prior_files_sha256'],observed)
            self.assertEqual(launcher.read_bytes(),before)
            self.assertEqual(installer.sha((active/'agent-v32.js').read_bytes()),observed['agent-v32.js'])

    def test_concurrent_operator_edit_aborts_before_activation(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);launcher=root/'harmonika';launcher.write_bytes(b'#!/bin/sh\nexec /bin/echo NATIVE "$@"\n');launcher.chmod(0o755)
            first=installer.activate(root,ROOT/'tools',shutil.which('node'))
            active=(root/'agent-v32/current').resolve()
            candidate=root/'candidate';candidate.mkdir()
            for name in installer.FILES:shutil.copy2(ROOT/'tools'/name,candidate/name)
            with (candidate/'agent-v32.js').open('a') as f:f.write('\n// reviewed fix\n')
            real_run=installer.subprocess.run
            def concurrent_edit(args,**kwargs):
                result=real_run(args,**kwargs)
                if args[-1]=='--help':
                    with (active/'agent-state.cjs').open('a') as f:f.write('\n// concurrent operator edit\n')
                return result
            with patch.object(installer.subprocess,'run',side_effect=concurrent_edit):
                with self.assertRaisesRegex(RuntimeError,'activation_compare_failed'):
                    installer.upgrade(root,candidate,shutil.which('node'),first['version'])
            self.assertEqual((root/'agent-v32/current').resolve(),active)

if __name__=='__main__':unittest.main()
