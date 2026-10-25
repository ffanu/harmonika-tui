import importlib.util,unittest
from pathlib import Path
from types import SimpleNamespace
s=importlib.util.spec_from_file_location('clipboard',Path(__file__).resolve().parents[1]/'runtime/tools/clipboard.py');m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
class Clipboard(unittest.TestCase):
 def test_plain_unicode_stdin_and_fixed_argv(self):
  seen=[]
  def run(argv,**kw):seen.append((argv,kw));return SimpleNamespace(returncode=0)
  result=m.copy_text('\x1b[31mHalo 🌍\x1b[0m\x1b]52;c;secret\x07'.encode(),run,['/fixture/pbcopy'])
  self.assertTrue(result['ok']);self.assertEqual(seen[0][1]['input'],'Halo 🌍'.encode());self.assertNotIn('Halo',str(seen[0][0]))
 def test_large_or_invalid_utf8_refused(self):
  for data in [b'x'*(m.MAX_BYTES+1),b'\xff']:
   with self.assertRaises((ValueError,UnicodeError)):m.copy_text(data,command=['/fixture/pbcopy'])
 def test_adapter_failure_no_payload_in_error(self):
  with self.assertRaisesRegex(ValueError,'^clipboard_failed$'):m.copy_text(b'secret',lambda *a,**kw:SimpleNamespace(returncode=1),['/fixture/pbcopy'])
