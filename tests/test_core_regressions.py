import json,unittest,tempfile
from pathlib import Path
from backend.core_engine import data_tools as d
from backend.core_engine.registry import dispatch,capabilities,descriptors,ALIASES
from backend.core_engine.state import Store
class Regressions(unittest.TestCase):
 def test_transform_malformed_no_exception_even_empty(self):
  for rows in ([],[{'a':1}]):
   for op in ({'type':[]},{'type':'sort','column':[]},{'type':'select','columns':1},{'type':'filter','column':{},'op':'eq','value':1}):
    out=d.dispatch('data.transform',{'rows':rows,'operations':[op]});self.assertEqual(out['status'],'error')
  out=d.dispatch('data.transform',{'rows':[{'a':2},{'a':1}],'operations':[{'type':'sort','column':'a'}]})
  self.assertEqual(out['data']['rows'],[{'a':1},{'a':2}])
 def test_numeric_exponent_cancellation_and_underflow(self):
  for text in ('1e999999999','1e-999999999','9'*129):
   self.assertEqual(d.dispatch('data.analyze',{'rows':[{'n':text}]})['status'],'error')
  out=d.dispatch('data.analyze',{'rows':[{'n':'1e308'},{'n':1},{'n':'-1e308'}]})
  self.assertAlmostEqual(out['data']['columns']['n']['mean'],1/3)
  out=d.dispatch('data.analyze',{'rows':[{'n':'1e-324'}]});self.assertNotEqual(out['data']['columns']['n']['mean'],0)
 def test_visualization_types_and_literal_field_names(self):
  out=d.dispatch('data.visualize',{'rows':[{'category.label':'A','amount.value':'12'}],'chart_type':'bar','x':'category.label','y':'amount.value'})
  s=out['data']['spec'];self.assertEqual(s['encoding']['x']['field'],'category\\.label');self.assertEqual(s['encoding']['y']['type'],'quantitative');self.assertEqual(s['data']['values'][0]['amount.value'],12)
  out=d.dispatch('data.visualize',{'rows':[{'a[0]':'1','y':'2'}],'chart_type':'scatter','x':'a[0]','y':'y'});self.assertEqual(out['data']['spec']['encoding']['x']['field'],'a\\[0\\]')
  self.assertEqual(d.dispatch('data.visualize',{'rows':[{'x':'A','y':2}],'chart_type':'scatter','x':'x','y':'y'})['status'],'error')
 def test_export_prefix_injection_after_whitespace_bom(self):
  for text in ('\n=HYPERLINK("evil")','\ufeff =evil','\u00a0=evil',' \t=evil'):
   out=d.dispatch('data.export',{'rows':[{'x':text}],'format':'csv'});self.assertEqual(out['status'],'completed');self.assertIn("'",out['data']['content'])
 def test_workflow_data_columns_not_control_fields(self):
  with tempfile.TemporaryDirectory() as temp:
   store=Store(Path(temp)/'private/state.sqlite',b'x'*32)
   steps=[{'id':'analyze','skill_id':'data.analyze','input':{'rows':[{'url':'https://example.invalid','amount':12}]}}]
   defined=store.execute('workflow.define',{'name':'data','steps':steps},'api-token:1','define0001')
   out=dispatch('workflow.run',{'workflow_id':defined['data']['workflow_id']},'api-token:1',store,'run000001')
   self.assertEqual(out['data']['status'],'completed')
 def test_canonical99_and_exact_aliases(self):
  self.assertEqual(len(descriptors()),99);self.assertEqual(len(ALIASES),19)
  self.assertEqual(capabilities()['readiness'],{'available':12,'context_required':19,'local_only':6,'native_endpoint':2,'unavailable':60})
if __name__=='__main__':unittest.main()
