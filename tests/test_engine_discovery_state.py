import importlib.util,unittest
from pathlib import Path
from unittest.mock import patch
spec=importlib.util.spec_from_file_location('discovery_state',Path(__file__).resolve().parents[1]/'runtime/tools/state_store.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
class StateSummary(unittest.TestCase):
 def test_no_key_does_not_query_network(self):
  with patch.object(Path,'exists',return_value=False):self.assertEqual(m.engine_discovery_status(),{'status':'not_configured'})
 def test_text_summary_is_compact(self):
  value={'ok':True,'engine_discovery':{'status':'ready','registered':256,'rejected':3}}
  self.assertIn('Engine discovery: ready | Aktif: 256 | Ditolak: 3',m.render_status(value))
 def test_failure_does_not_break_session_status(self):
  with patch.object(Path,'exists',return_value=True),patch('importlib.util.spec_from_file_location',side_effect=OSError('PRIVATE')):
   self.assertEqual(m.engine_discovery_status(),{'status':'unavailable'})
