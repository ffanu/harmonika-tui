import importlib.util,unittest,sys
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'backend'))
from erp_engine.operations import ANALYSIS_OPS
s=importlib.util.spec_from_file_location('erp_cli_qa',ROOT/'runtime/tools/erp_cli.py');m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
class Client(unittest.TestCase):
 def test_pure_busy_retry_reuses_request_key_and_body(self):
  self.assertEqual(set(m.ANALYSIS_OPERATIONS),ANALYSIS_OPS)
  busy={'error':{'code':'analysis_busy'}}
  with patch.object(m.client,'request',side_effect=[busy,busy,{'result':{'ok':True}}]) as req,patch.object(m.time,'sleep') as sleep:
   value=m.pure_request('erp_all.plan',{'spec':'Synthetic QA'});self.assertTrue(value['result']['ok']);self.assertEqual(req.call_count,3);self.assertEqual(len({call.kwargs['idempotency_key'] for call in req.call_args_list}),1);self.assertEqual(req.call_args_list[0].args,req.call_args_list[-1].args);self.assertEqual([x.args[0] for x in sleep.call_args_list],[.25,.5])
 def test_other_errors_and_non_pure_tools_are_not_retried(self):
  for skill,value in [('erp_all.plan',{'error':{'code':'invalid_input'}}),('other.write',{'error':{'code':'analysis_busy'}})]:
   with patch.object(m.client,'request',return_value=value) as req,patch.object(m.time,'sleep') as sleep:
    self.assertEqual(m.pure_request(skill,{}),value);self.assertEqual(req.call_count,1);sleep.assert_not_called()
 def test_contention_is_bounded_to_four_admission_attempts(self):
  busy={'error':{'code':'analysis_busy'}}
  with patch.object(m.client,'request',return_value=busy) as req,patch.object(m.time,'sleep') as sleep:
   self.assertEqual(m.pure_request('erp_all.catalog',{}),busy);self.assertEqual(req.call_count,4);self.assertEqual(sleep.call_count,3)
