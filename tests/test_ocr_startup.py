import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

FILE = Path(__file__).resolve().parents[1]/'backend/ocr_rtx/harden_startup.py'
spec = importlib.util.spec_from_file_location('ocr_startup_tests', FILE)
startup = importlib.util.module_from_spec(spec); spec.loader.exec_module(startup)


class StartupTests(unittest.TestCase):
    def test_existing_crlf_wrapper_can_upgrade_and_canonical_rerun_is_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); control = root/'control'; control.mkdir()
            keep = root/'keep'; keep.mkdir(); launcher = root/'start.ps1'
            old = "$ErrorActionPreference='Stop'\r\n& 'C:\\ProgramData\\Harmonika\\RTXKeepAlive\\OCRRuntime.ps1'\r\n"
            launcher.write_bytes(old.encode())
            (keep/'components.json').write_text(json.dumps({'managed_tasks': ['existing']}))
            def path(value):
                return {r'C:\ProgramData\Harmonika\RTXKeepAlive': keep,
                        r'C:\Users\Kraken\harmonika-vision-worker\start-runtime.ps1': launcher}.get(str(value), Path(value))
            with patch.object(startup, 'Path', path), patch.object(startup, '__file__', str(control/'harden.py')), patch.object(startup, 'os', SimpleNamespace(name='nt')), patch.object(startup.subprocess, 'run', return_value=SimpleNamespace(returncode=0)):
                startup.main(); first = launcher.read_bytes()
                self.assertIn(b'Start-ScheduledTask -TaskName HarmonikaOCRRuntime', first)
                # Simulate Windows newline conversion on the already-updated file.
                launcher.write_bytes(first.replace(b'\n', b'\r\n'))
                startup.main()
                self.assertEqual(launcher.read_bytes().replace(b'\r\n', b'\n'), first)
            self.assertEqual((control/'start-runtime.before-ocr.ps1').read_bytes(), old.encode())
            self.assertEqual(json.loads((keep/'components.json').read_text())['managed_tasks'],
                             ['existing', 'HarmonikaStatelessOCR'])

    def test_unrecognized_operator_launcher_is_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); launcher = root/'start.ps1'; original = b'# operator custom script\r\n'; launcher.write_bytes(original)
            def path(value):
                return launcher if str(value).endswith('start-runtime.ps1') else Path(value)
            with patch.object(startup, 'Path', path), patch.object(startup, 'os', SimpleNamespace(name='nt')):
                with self.assertRaisesRegex(SystemExit, 'preserved'):
                    startup.main()
            self.assertEqual(launcher.read_bytes(), original)


if __name__ == '__main__':
    unittest.main()
