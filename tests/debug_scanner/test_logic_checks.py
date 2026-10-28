import sys,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'backend'))
from debug_scanner.logic_checks import logic_scan
class Logic(unittest.TestCase):
 def test_state_trace_detects_unlisted_transition_and_terminal_reentry(self):
  v={'states':['OPEN','DONE'],'initial':'OPEN','terminal_states':['DONE'],'transitions':[{'from':'OPEN','to':'DONE','event':'finish'}],'trace':[{'from':'OPEN','to':'DONE','event':'finish'}]};r=logic_scan('logic.state.machine',v);self.assertTrue(r['data']['model_valid']);self.assertFalse(r['business_code_executed']);v['trace'].append({'from':'DONE','to':'OPEN','event':'reset'});r=logic_scan('logic.state.machine',v);self.assertEqual({x['rule_id'] for x in r['data']['violations']},{'STATE.TRANSITION_AFTER_TERMINAL','STATE.UNDECLARED_TRANSITION'})
 def test_state_discontinuity_and_invalid_state(self):
  v={'states':['A','B'],'initial':'A','terminal_states':[],'transitions':[],'trace':[{'from':'B','to':'A','event':'go'}]};self.assertTrue(logic_scan('logic.state.machine',v)['data']['violations'])
  v['trace'][0]['to']='unknown'
  with self.assertRaises(ValueError):logic_scan('logic.state.machine',v)
 def test_workflow_cycles_and_dependencies(self):
  v={'nodes':['a','b'],'dependencies':[{'node':'b','requires':'a'}],'trace':[{'node':'b','action':'start'}]};r=logic_scan('logic.workflow.bug',v);self.assertFalse(r['data']['cycle_detected']);self.assertEqual(r['data']['violations'][0]['rule_id'],'WORKFLOW.DEPENDENCY_NOT_COMPLETED')
  v['trace']=[{'node':'a','action':'start'},{'node':'a','action':'complete'},{'node':'b','action':'start'},{'node':'b','action':'complete'}];self.assertFalse(logic_scan('logic.workflow.bug',v)['data']['violations']);v['dependencies'].append({'node':'a','requires':'b'});self.assertTrue(logic_scan('logic.workflow.bug',v)['data']['cycle_detected'])
 def test_failed_node_does_not_satisfy_dependency_and_duplicate_complete(self):
  v={'nodes':['a','b'],'dependencies':[{'node':'b','requires':'a'}],'trace':[{'node':'a','action':'start'},{'node':'a','action':'fail'},{'node':'b','action':'start'},{'node':'b','action':'complete'},{'node':'b','action':'complete'}]};rules={x['rule_id'] for x in logic_scan('logic.workflow.bug',v)['data']['violations']};self.assertIn('WORKFLOW.DEPENDENCY_NOT_COMPLETED',rules);self.assertIn('WORKFLOW.DUPLICATE_TERMINAL_EVENT',rules)
 def test_consistency_duplicate_and_dangling_refs(self):
  v={'entities':[{'type':'user','id':'a'},{'type':'user','id':'a'}],'references':[{'from_type':'user','from_id':'a','to_type':'invoice','to_id':'missing'}]};r=logic_scan('logic.data.consistency',v);self.assertEqual(len(r['data']['violations']),2);self.assertFalse(r['data']['database_read']);self.assertNotIn('missing',str(r['data']))
 def test_exact_calculation_and_rounding(self):
  v={'lines':[{'id':'a','quantity':3,'unit_minor':'15'}],'tax_bps':1000,'reported_subtotal_minor':'45','reported_tax_minor':'5','reported_total_minor':'50'};self.assertFalse(logic_scan('logic.calculation.error',v)['data']['violations']);v['reported_tax_minor']='4';self.assertEqual(logic_scan('logic.calculation.error',v)['data']['violations'][0]['field'],'tax_minor')
 def test_calculation_int64_overflow_and_duplicate_lines(self):
  v={'lines':[{'id':'a','quantity':2,'unit_minor':'9223372036854775807'}],'tax_bps':0,'reported_subtotal_minor':'0','reported_tax_minor':'0','reported_total_minor':'0'}
  with self.assertRaises(ValueError):logic_scan('logic.calculation.error',v)
  v['lines']=[{'id':'a','quantity':1,'unit_minor':'1'}]*2
  with self.assertRaises(ValueError):logic_scan('logic.calculation.error',v)
 def test_typed_json_case_comparison(self):
  r=logic_scan('logic.edge.case',{'cases':[{'id':'typed','expected':True,'observed':1},{'id':'reordered','expected':{'a':1,'b':2},'observed':{'b':2,'a':1}}]});self.assertEqual(r['data']['mismatch_count'],1);self.assertFalse(r['data']['cases_executed'])
 def test_api_contract_types_missing_extra_no_values(self):
  r=logic_scan('logic.integration.mismatch',{'fields':[{'name':'id','kind':'integer','required':True}],'records':[{'id':'a','payload':{'id':True,'secret':'PRIVATEVALUE'}},{'id':'b','payload':{}}],'allow_extra':False});self.assertEqual(len(r['data']['violations']),3);self.assertNotIn('PRIVATEVALUE',str(r));self.assertFalse(r['data']['api_called'])
 def tx(self,**kw):return {'id':'a','resource':'stock','started':0,'finished':5,'read_version':0,'write_version':1,'committed':True,**kw}
 def test_race_double_commit_and_aborted_writer(self):
  v={'initial_versions':[{'resource':'stock','version':0}],'transactions':[self.tx(),self.tx(id='b',started=1,finished=6)]};r=logic_scan('logic.race.condition',v);self.assertEqual(len(r['data']['conflicting_commit_pairs']),1);self.assertFalse(r['data']['runtime_race_reproduced']);v['transactions'][1]['committed']=False;self.assertFalse(logic_scan('logic.race.condition',v)['data']['conflicting_commit_pairs'])
 def test_concurrent_cas_version_chain_and_ambiguous_order(self):
  v={'initial_versions':[{'resource':'stock','version':0}],'transactions':[self.tx(),self.tx(id='b',started=5,finished=10,read_version=1,write_version=2)]};self.assertFalse(logic_scan('logic.concurrency',v)['data']['violations']);v['transactions'][1]['read_version']=0;v['transactions'][1]['write_version']=1;self.assertTrue(logic_scan('logic.concurrency',v)['data']['violations']);v['transactions'][1].update(finished=5,read_version=1,write_version=2);r=logic_scan('logic.concurrency',v);self.assertFalse(r['data']['violations']);self.assertTrue(r['data']['commit_order_ambiguous_resource_refs'])
 def test_no_privileged_fields_or_arbitrary_python(self):
  for v in ({'cases':[],'command':'whoami'},{'cases':[{'id':'a','expected':float('nan'),'observed':1}]}):
   with self.assertRaises(ValueError):logic_scan('logic.edge.case',v)
