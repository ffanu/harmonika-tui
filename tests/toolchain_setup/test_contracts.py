import importlib.util,json,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
s=importlib.util.spec_from_file_location('catalog',ROOT/'backend/toolchain_setup/catalog.py');m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
class Tests(unittest.TestCase):
 def test_all_19_parts_accounted_for(self):
  d=json.loads((ROOT/'backend/toolchain_setup/coverage.json').read_text());self.assertEqual({x['part'] for x in d['parts']},set(range(1,20)));self.assertTrue(all(x['decision']!='review_required' for x in d['sections']))
 def test_core_has_no_gpu_or_host_installer(self):
  req=(ROOT/'backend/toolchain_setup/requirements.in').read_text().splitlines()
  for p in ['torch','vllm','TTS','bark','asyncio','zipfile36','google-generativeai']:self.assertNotIn(p,req)
  src=(ROOT/'backend/toolchain_setup/setup.py').read_text();self.assertNotIn('--break-system-packages',src);self.assertNotIn('apt-get upgrade',src);self.assertNotIn('curl',src)
 def test_fixed_loopback_rpc_has_no_caller_url(self):
  s=(ROOT/'backend/toolchain_setup/rpc_proxy.py').read_text();self.assertIn('127.0.0.1:18319',s);self.assertIn("set(data)-{'operation','owner_context'}",s)
 def test_offline_wheel_tampering_rejected_before_pip(self):
  import tempfile
  from unittest.mock import patch
  spec=importlib.util.spec_from_file_location('isolated_setup',ROOT/'backend/toolchain_setup/setup.py');setup=importlib.util.module_from_spec(spec);spec.loader.exec_module(setup)
  with tempfile.TemporaryDirectory() as d:
   r=Path(d);(r/'venv/bin').mkdir(parents=True);(r/'venv/bin/python').touch();(r/'wheels').mkdir();(r/'wheels/fake.whl').write_bytes(b'changed');(r/'requirements.lock').write_text('fake==1');(r/'wheel-sha256.json').write_text(json.dumps({'fake.whl':'a'*64}))
   with patch.object(setup,'run') as run:
    with self.assertRaisesRegex(ValueError,'wheel_integrity_failed'):setup.install(r)
    run.assert_not_called()
if __name__=='__main__':unittest.main()

