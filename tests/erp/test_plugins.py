import asyncio,importlib.util,json,sys,tempfile,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'backend'))
s=importlib.util.spec_from_file_location('bundle',ROOT/'scripts/bundle-erp-plugins.py');bundle=importlib.util.module_from_spec(s);s.loader.exec_module(bundle)
from engine_registry.discovery import Discovery
class Plugins(unittest.IsolatedAsyncioTestCase):
 async def test_reviewed_bundle_discovery_and_real_invoke(self):
  with tempfile.TemporaryDirectory() as temp:
   root=Path(temp).resolve();result=bundle.bundle(root/'plugins');expected=len(bundle.planner.AVAILABLE|bundle.operations.GENERATOR_OPS|bundle.operations.ANALYSIS_OPS|{'plan','generate','catalog'});self.assertEqual(result['plugins'],expected)
   d=Discovery(root/'plugins',root/'discovery.sqlite',reserved_ids=[])
   await d.refresh();status=d.summary();self.assertEqual(status['registered'],expected,status)
   result=await d.execute('erp_all.plan',{'spec':'Saya punya ISP 500 pelanggan','company':'QA'})
   self.assertEqual(result['industry'],'isp')
   result=await d.execute('erp_all.generate',{'spec':'Toko retail kasir','industry':'retail','company':'QA'})
   self.assertIn('artifact',result);self.assertNotIn('files',result);self.assertFalse(result['deployed'])
   result=await d.execute('erp_all.finance.budgeting',{'data':{'rows':[{'account':'expense','budget_minor':'100','actual_minor':'120'}]}})
   self.assertEqual(result['result']['variance_minor'],'20');self.assertFalse(result['posted'])
   result=await d.execute('erp_all.isp.billing.cycle',{'data':{'start':'2028-01-31','frequency':'monthly','count':3}})
   self.assertEqual(result['result']['dates'],['2028-01-31','2028-02-29','2028-03-31'])
   result=await d.execute('erp_all.mfg.mrp',{'data':{'bom':[{'item':'a','components':[{'item':'b','quantity':2}]}],'demand':[{'item':'a','quantity':5}],'stock':[]}})
   self.assertEqual(result['result']['requirements'][1]['net_quantity'],10)
   report={'period':'2026-10','currency':'IDR','accounts':[{'code':'cash','type':'asset','is_cash':True},{'code':'capital','type':'equity','is_cash':False}],'journals':[{'id':'opening','period':'2026-10','status':'POSTED','cashflow_category':'financing','lines':[{'account_code':'cash','debit_minor':'100','credit_minor':'0'},{'account_code':'capital','debit_minor':'0','credit_minor':'100'}]}]}
   result=await d.execute('erp_all.finance.financial.statement',{'data':report});self.assertEqual(result['result']['balance_sheet']['imbalance_minor'],'0');self.assertEqual(result['result']['cash_flow']['closing_minor'],'100');self.assertFalse(result['posted'])
   result=await d.execute('erp_all.hr.payroll',{'data':{'employees':[{'id':'e','earnings':[{'component':'base','amount_minor':'1000'}],'deductions':[]}]}});self.assertEqual(result['result']['net_minor'],'1000');self.assertFalse(result['result']['disbursement_sent'])
   result=await d.execute('erp_all.report.export.excel',{'data':{'title':'QA','headers':['ID'],'rows':[['0001']]}});self.assertEqual(result['result']['row_count'],1);self.assertEqual(result['result']['artifact']['name'],'harmonika-report.xlsx');self.assertFalse(result['result']['formulas_enabled'])

if __name__=='__main__':unittest.main()
