import tempfile,unittest
from pathlib import Path
from backend.telco_engine.runtime import Runtime
from backend.telco_engine.common import DomainError
class Planning(unittest.TestCase):
 def setUp(self):self.tmp=tempfile.TemporaryDirectory();self.rt=Runtime(Path(self.tmp.name)/'state.sqlite');self.n=0
 def tearDown(self):self.tmp.cleanup()
 def invoke(self,id,args):
  self.n+=1;r=self.rt.invoke('api-token:740',id,args,'t'+str(self.n))
  if r['status']=='approval_required':r=self.rt.approve('api-token:740',r['id'],r['input_hash'])
  return r
 def test_workflow_cycle_does_not_persist_partial_template(self):
  r=self.invoke('workflow.templating',{'name':'Cycle','steps':[{'id':'a','skill_id':'report.arpu','depends_on':['b']},{'id':'b','skill_id':'report.arpu','depends_on':['a']}]});self.assertEqual(r['status'],'failed');self.assertEqual(r['error']['code'],'workflow_cycle')
 def test_dag_template_and_instance_are_explicit_manual_dispatch(self):
  r=self.invoke('workflow.templating',{'name':'Safe','steps':[{'id':'a','skill_id':'report.arpu','depends_on':[]},{'id':'b','skill_id':'bill.invoice.generate','depends_on':['a']}]});self.assertEqual(r['status'],'completed');t=r['result']
  r=self.invoke('workflow.trigger',{'template_id':t['id'],'expected_version':t['version'],'event_reference':'fixture'});self.assertEqual(r['result']['status'],'awaiting_step_dispatch');self.assertFalse(r['result']['automatic_actions_enabled'])
 def test_comparison_has_no_eval(self):
  r=self.invoke('workflow.condition',{'operator':'eq','left':'x','right':'x'});self.assertTrue(r['result']['matched']);self.assertFalse(r['result']['expression_evaluated'])
  with self.assertRaises(DomainError):self.invoke('workflow.condition',{'operator':'eval','left':'x','right':'x'})
 def test_pool_overlap_rejected(self):
  args={'name':'Fixture','prefix':'6221','suffix_digits':5,'start':0,'count':10}
  self.assertEqual(self.invoke('numbering.plan.manage',args)['status'],'completed')
  r=self.invoke('numbering.plan.manage',args);self.assertEqual(r['status'],'failed');self.assertEqual(r['error']['code'],'overlapping_pool')
 def test_critical_path_actual_calculation(self):
  r=self.invoke('workflow.optimization',{'steps':[{'id':'a','skill_id':'report.arpu','depends_on':[],'duration_seconds':5},{'id':'b','skill_id':'report.arpu','depends_on':[],'duration_seconds':7},{'id':'c','skill_id':'report.arpu','depends_on':['a','b'],'duration_seconds':2}]})
  self.assertEqual(r['result']['critical_path_seconds'],9);self.assertEqual(r['result']['serial_seconds'],14);self.assertFalse(r['result']['parallel_execution_performed'])
 def test_capacity_sizing_does_not_order_hardware(self):
  r=self.invoke('capacity.upgrade.recommend',{'peak_demand_mbps':800,'target_utilization_percent':80,'current_capacity_mbps':500,'source':'fixture'})
  self.assertEqual(r['result']['required_capacity_mbps'],1000);self.assertFalse(r['result']['hardware_ordered'])
