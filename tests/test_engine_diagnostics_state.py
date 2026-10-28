import importlib.util,json,math,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('state_diag',ROOT/'runtime/tools/state_store.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
class DiagnosticsState(unittest.TestCase):
 def test_mechanical_status_and_errors(self):
  d={'graphics':{'status':'busy','latency_ms':42.2},'registry_errors':{'status':'ok','count':1,'recent':[{'timestamp':'2026-10-25T04:00:00Z','skill_id':'text.metrics','error_type':'invalid_input','latency':3.2}]},'graphics_errors':{'status':'ok','count':1,'recent':[{'timestamp':'2026-10-25T04:00:01Z','skill_id':'image.generate','error_type':'graphics_busy','latency':4.5}]}}
  text=m.render_status({'ok':True,'engine_diagnostics':d})
  self.assertIn('Graphics Engine PC 2: Sibuk',text);self.assertIn('Error skill terisolasi: 2',text);self.assertIn('image.generate | graphics_busy',text)
 def test_untrusted_fields_never_rendered(self):
  sentinel='PRIVATE_CHAT_CUSTOMER_TOKEN'
  value={'graphics':{'status':sentinel,'endpoint':sentinel},'registry_errors':{'status':'ok','count':1,'recent':[{'timestamp':sentinel,'skill_id':sentinel,'error_type':sentinel,'latency':1,'prompt':sentinel}]},'graphics_errors':{'status':'ok','recent':[]}}
  result=m.sanitize_engine_diagnostics(value);self.assertNotIn(sentinel,json.dumps(result));self.assertEqual(result['graphics']['status'],'unknown')
 def test_bad_payload_types_and_nan_are_isolated(self):
  for value in [None,[],{'graphics':{'status':{}}},{'registry_errors':{'recent':[{},None]}},{'graphics':{'status':'online','latency_ms':float('nan')}}]:
   result=m.sanitize_engine_diagnostics(value);self.assertIsInstance(result,dict);self.assertNotIn('NaN',json.dumps(result))
 def test_offline_mode_is_not_claimed_as_real_offline(self):
  text=m.render_status({'ok':True,'engine_diagnostics':{'graphics':{'status':'not_checked'}}})
  self.assertIn('Tidak diperiksa (mode offline)',text)
if __name__=='__main__':unittest.main()
