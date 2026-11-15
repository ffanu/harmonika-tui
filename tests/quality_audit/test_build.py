from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from backend.quality_audit.build_gateway import build


class PhysicalBuild(unittest.TestCase):
    def test_symlink_build_uses_physical_pwd(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);physical=root/'release';physical.mkdir();logical=root/'current';logical.symlink_to(physical,target_is_directory=True)
            overlay=root/'overlay.json';overlay.write_text('{}');output=root/'gateway'
            def run(args,**kw):
                if args[1]=='build':
                    self.assertEqual(kw['cwd'],physical.resolve())
                    self.assertEqual(kw['env']['PWD'],str(physical.resolve()))
                    output.write_bytes(b'synthetic compiled fixture')
                    return subprocess.CompletedProcess(args,0,'','')
                return subprocess.CompletedProcess(args,0,'000 T main.qualityFromEnvironment\n','')
            with patch('subprocess.run',side_effect=run):result=build(logical,overlay,output)
            self.assertTrue(result['instrumentation_link_verified'])

    def test_successful_go_exit_without_linked_hook_is_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);overlay=root/'overlay.json';overlay.write_text('{}');output=root/'gateway';output.write_bytes(b'uninstrumented fixture')
            with patch('subprocess.run',return_value=subprocess.CompletedProcess([],0,'no instrumentation','')):
                with self.assertRaisesRegex(ValueError,'instrumentation_not_linked'):build(root,overlay,output)
            self.assertFalse((root/'quality-build-proof.json').exists())


if __name__=='__main__':unittest.main()
