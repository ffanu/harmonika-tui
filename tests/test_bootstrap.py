import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

SOURCE=Path(__file__).resolve().parent.parent
spec=importlib.util.spec_from_file_location('installer',SOURCE/'runtime/install.py');installer=importlib.util.module_from_spec(spec);spec.loader.exec_module(installer)

class Bootstrap(unittest.TestCase):
    def test_failed_preflight_cleans_staging_and_allows_retry(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);binary=root/'harmonika';binary.write_bytes(b'fixture binary')
            manifest={'release':'2026.10.24.1','binary_sha256':installer.m.digest(binary),'files':{'tools/helper.cjs':{'sha256':'unused'}}}
            package=root/'runtime.zip'
            with zipfile.ZipFile(package,'w') as out:
                out.writestr('release.json',json.dumps(manifest));out.writestr('tools/helper.cjs','invalid syntax intentionally')
            with patch.object(installer,'ROOT',root),patch.object(installer,'BINARY',binary),patch.object(installer.m,'verify_envelope',side_effect=lambda data,*args:json.loads(data)),patch.object(installer.m,'preflight',side_effect=ValueError('expected_preflight_failure')):
                for _ in range(2):
                    with self.assertRaisesRegex(ValueError,'expected_preflight_failure'):installer.prepare(package)
                    self.assertEqual(list((root/'versions').iterdir()),[])
                    self.assertFalse((root/'current').exists())
    def test_bootstrap_refuses_traversal_before_extract(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);package=root/'runtime.zip'
            with zipfile.ZipFile(package,'w') as out:out.writestr('../escape','bad');out.writestr('release.json','{}')
            with patch.object(installer,'ROOT',root):
                with self.assertRaisesRegex(ValueError,'unsafe_path'):installer.prepare(package)
            self.assertFalse((root.parent/'escape').exists());self.assertEqual(list((root/'versions').iterdir()),[])

if __name__=='__main__':unittest.main()
