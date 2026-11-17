import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

SOURCE=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('portable_installer',SOURCE/'runtime/install.py')
installer=importlib.util.module_from_spec(spec);spec.loader.exec_module(installer)

class InstallerPortability(unittest.TestCase):
    def test_signed_catalog_platform_mapping_and_unsupported_target(self):
        for system,machine,expected in [('Darwin','arm64','darwin-arm64'),('Darwin','x86_64','darwin-x64'),('Linux','aarch64','linux-arm64'),('Linux','x86_64','linux-x64'),('Windows','AMD64','windows-x64')]:
            with patch.object(installer.platform,'system',return_value=system),patch.object(installer.platform,'machine',return_value=machine):
                self.assertEqual(installer.detect_platform(),expected)
        with patch.object(installer.platform,'system',return_value='Linux'),patch.object(installer.platform,'machine',return_value='riscv64'):
            with self.assertRaisesRegex(ValueError,'unsupported_platform'):installer.detect_platform()

    def test_old_node_is_rejected_before_touching_installation(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)/'not-created'
            with patch.object(installer,'ROOT',root),patch.object(installer.subprocess,'run',return_value=subprocess.CompletedProcess([],0,'17\n','')),patch.object(installer.m,'active') as active:
                with self.assertRaisesRegex(ValueError,'node_18_or_newer_required'):installer.main()
                active.assert_not_called()
            self.assertFalse(root.exists())

    def test_legacy_without_npm_has_actionable_error(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            with patch.dict(os.environ,{'HARMONIKA_TOOLS_HOME':str(root/'missing-shared')}),patch.object(Path,'home',return_value=root),patch.object(installer.shutil,'which',return_value=None),patch.object(installer.subprocess,'run') as run:
                with self.assertRaisesRegex(ValueError,'legacy_package_requires_npm'):installer.ensure_dependencies(root)
                run.assert_not_called()

    def test_windows_wrappers_are_private_install_scope_and_utf8(self):
        with tempfile.TemporaryDirectory() as temp:
            parent=Path(temp);root=parent/'terminal é space';root.mkdir();bindir=parent/'bin é space';system=parent/'system';(system/'System32').mkdir(parents=True)
            with patch.dict(os.environ,{'HARMONIKA_BIN_DIR':str(bindir),'SystemRoot':str(system)}):
                installer.write_windows_wrappers(root)
            command=(bindir/'harmonika.cmd').read_text(encoding='utf-8')
            self.assertIn('terminal é space',command);self.assertIn('%*',command);self.assertIn('chcp 65001',command)
            self.assertFalse((system/'System32/harmonika.cmd').exists())
            self.assertTrue((root/'node.path').is_file())

    @unittest.skipUnless(os.name=='posix' and shutil.which('node') and shutil.which('git'),'POSIX bootstrap fixture')
    def test_bootstrap_preserves_existing_non_repository_directory(self):
        with tempfile.TemporaryDirectory() as temp:
            source=Path(temp)/'existing source';source.mkdir();marker=source/'keep.txt';marker.write_text('keep')
            env=dict(os.environ,HARMONIKA_SOURCE_DIR=str(source),HARMONIKA_NONINTERACTIVE='1')
            run=subprocess.run(['sh',str(SOURCE/'scripts/install.sh')],env=env,stdin=subprocess.DEVNULL,capture_output=True,text=True,timeout=15)
            self.assertNotEqual(run.returncode,0);self.assertEqual(marker.read_text(),'keep')

    @unittest.skipUnless(os.name=='posix','POSIX provider command quoting')
    def test_configuration_paths_with_spaces_are_quoted_without_shell_expansion(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);cfg=root/'profile space $(touch UNEXPECTED)';cfg.mkdir()
            key=cfg/'harmonika-api-key';key.write_text('synthetic-marker')
            env={'HARMONIKA_GLOBAL_CONFIG':str(cfg),'HARMONIKA_CONFIG_DIR':str(cfg),'HARMONIKA_GLOBAL_DATA':str(root/'share'),'HARMONIKA_DOWNLOADS_DIR':str(root/'downloads')}
            with patch.dict(os.environ,env):installer.ensure_default_config(root/'terminal')
            command=json.loads((cfg/'harmonika.json').read_text())['providers']['harmonika']['api_key']
            run=subprocess.run(['sh','-c','printf %s "'+command+'"'],cwd=root,capture_output=True,text=True,timeout=5)
            self.assertEqual(run.returncode,0);self.assertEqual(run.stdout,'synthetic-marker')
            self.assertFalse((root/'UNEXPECTED').exists())

if __name__=='__main__':unittest.main()
