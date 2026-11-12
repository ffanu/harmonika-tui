from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

WRAPPER=Path(__file__).resolve().parents[1]/'backend/rtx_queue/windows/ChildRuntime.py'


class ChildRuntime(unittest.TestCase):
    def test_arguments_imports_exit_code_and_private_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'helper.py').write_text('value=42\n')
            script=root/'app with spaces.py';script.write_text('import helper,sys\nassert helper.value==42\nassert sys.argv[1:]==["--port","18190"]\nprint("PRIVATE_SENTINEL")\nprint("PRIVATE_SENTINEL",file=sys.stderr)\nraise SystemExit(7)\n')
            result=subprocess.run([sys.executable,str(WRAPPER),'-u',str(script),'--port','18190'],capture_output=True,timeout=10)
            self.assertEqual(result.returncode,7);self.assertEqual(result.stdout,b'');self.assertEqual(result.stderr,b'')

    def test_app_survives_closed_parent_output_pipes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);marker=root/'done';script=root/'writer.py'
            script.write_text('import time,sys\nfrom pathlib import Path\ntime.sleep(.2)\nfor i in range(50):\n print("PRIVATE_SENTINEL",flush=True)\n print("PRIVATE_SENTINEL",file=sys.stderr,flush=True)\nPath(sys.argv[1]).write_text("done")\n')
            child=subprocess.Popen([sys.executable,str(WRAPPER),str(script),str(marker)],stdout=subprocess.PIPE,stderr=subprocess.PIPE)
            child.stdout.close();child.stderr.close()
            self.assertEqual(child.wait(timeout=10),0);self.assertEqual(marker.read_text(),'done')

    def test_missing_app_fails_without_output(self):
        result=subprocess.run([sys.executable,str(WRAPPER)],capture_output=True,timeout=10)
        self.assertEqual(result.returncode,2);self.assertEqual(result.stdout,b'');self.assertEqual(result.stderr,b'')


if __name__=='__main__':unittest.main()
