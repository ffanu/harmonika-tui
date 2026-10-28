import importlib.util,json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[2]
s=importlib.util.spec_from_file_location('h3_qualification',ROOT/'backend/minimax_h3/qualify_runtime.py');m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
class Contracts(unittest.TestCase):
 def test_pinned_model_and_torch_metadata(self):
  rows=json.loads((ROOT/'backend/minimax_h3/models.json').read_text())['files'];self.assertEqual(len(rows),5)
  for row in rows:self.assertRegex(row['sha256'],r'^[0-9a-f]{64}$');self.assertGreater(row['bytes'],1000000);self.assertNotIn('..',row['file'])
  rows=json.loads((ROOT/'backend/minimax_h3/torch-wheels.json').read_text())
  for row in rows:self.assertRegex(row['sha256'],r'^[0-9a-f]{64}$');self.assertIn('pytorch.org/',row['url']);self.assertIn('cp312',row['file'])
 def test_invalid_or_forbidden_port_never_requests_network(self):
  for p in [3000,0,-1,65536,True,'3001']:
   with patch.object(m.urllib.request,'build_opener') as opener:
    with self.assertRaises(AssertionError):m.request(p,'/system_stats')
    opener.assert_not_called()
 def test_artifact_paths_deny_traversal_and_wrong_type(self):
  with tempfile.TemporaryDirectory() as temp,patch.object(m,'ROOT',Path(temp)):
   for value in [{'filename':'../../../escape.mp4'},{'filename':'bad.exe'},{'filename':'/tmp/escape.mp4'}]:
    with self.assertRaises(m.QualificationError):m.artifact_paths({'outputs':{'one':{'videos':[value]}}})
   got=m.artifact_paths({'outputs':{'one':{'videos':[{'filename':'owned.mp4','subfolder':'test','type':'output'}]}}});self.assertEqual(got[0],(Path(temp)/'ComfyUI/output/test/owned.mp4').resolve())
 def test_state_starts_unregistered_and_unverified(self):
  with tempfile.TemporaryDirectory() as temp,patch.object(m,'STATE',Path(temp)/'status.json'):
   m.state('starting_runtime',technical_render_verified=False);r=json.loads(m.STATE.read_text());self.assertFalse(r['public_route_registered']);self.assertFalse(r['customer_data_used']);self.assertFalse(r['technical_render_verified'])
