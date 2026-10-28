import sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'backend'))
from erp_engine.operations import invoke
from erp_engine.planner import ERPError,capabilities
class FinancialAnalysis(unittest.TestCase):
 def analyze(self,op,v):
  r=invoke(op,v);self.assertFalse(r['posted']);self.assertFalse(r['live_database_access']);return r['result']
 def test_reconciliation_does_not_guess_ambiguous_matches(self):
  def row(i):return {'id':i,'reference':'VA-1','amount_minor':'100','direction':'in','currency':'IDR'}
  r=self.analyze('finance.reconciliation',{'bank':[row('b1'),row('b2')],'ledger':[row('l1')]});self.assertEqual(r['matches'],[]);self.assertEqual(len(r['ambiguous']),1);self.assertFalse(r['settlement_verified'])
 def test_reconciliation_currency_and_direction_required(self):
  b={'id':'b','reference':'r','amount_minor':'100','direction':'in','currency':'IDR'};l={**b,'id':'l','currency':'USD'}
  r=self.analyze('finance.reconciliation',{'bank':[b],'ledger':[l]});self.assertEqual(r['matches'],[]);self.assertEqual(r['unmatched'],{'bank':['b'],'ledger':['l']})
 def test_reconciliation_exact_unique_match(self):
  b={'id':'b','reference':'r','amount_minor':'100','direction':'in','currency':'IDR'}
  self.assertEqual(self.analyze('finance.reconciliation',{'bank':[b],'ledger':[{**b,'id':'l'}]})['matches'],[{'bank_id':'b','ledger_id':'l'}])
 def test_depreciation_conserves_every_minor_unit(self):
  for method in ['straight_line','declining']:
   v={'cost_minor':'1001','residual_minor':'100','periods':12,'method':method}
   if method=='declining':v['rate_bps']=2000
   r=self.analyze('finance.depreciation',v);self.assertEqual(sum(int(x['depreciation_minor']) for x in r['schedule']),901);self.assertEqual(r['schedule'][-1]['book_minor'],'100')
 def test_budget_rejects_duplicate_accounts(self):
  row={'account':'expense','budget_minor':'100','actual_minor':'120'}
  self.assertEqual(self.analyze('finance.budgeting',{'rows':[row]})['variance_minor'],'20')
  with self.assertRaises(ERPError):invoke('finance.budgeting',{'rows':[row,row]})
 def test_currency_respects_minor_scales(self):
  r=self.analyze('finance.multi.currency',{'amount_minor':'123','rate_numerator':'16000','rate_denominator':'1','source_decimals':2,'target_decimals':0});self.assertEqual(r['converted_minor'],'19680')
 def test_aging_due_today_is_not_overdue(self):
  v={'as_of':'2026-10-26','invoices':[{'id':'1','due':'2026-10-26','total_minor':'100','paid_minor':'40'},{'id':'2','due':'2026-09-25','total_minor':'99','paid_minor':'0'}]}
  for op in ['report.customer.aging','report.vendor.aging']:
   r=self.analyze(op,v);self.assertEqual(r['buckets_minor']['not_due'],'60');self.assertEqual(r['buckets_minor']['31_60'],'99');self.assertEqual(r['total_outstanding_minor'],'159')
 def test_invalid_dates_and_overpayment_fail(self):
  with self.assertRaises(ERPError):invoke('report.customer.aging',{'as_of':'2026-02-30','invoices':[]})
  with self.assertRaises(ERPError):invoke('report.customer.aging',{'as_of':'2026-10-26','invoices':[{'id':'1','due':'2026-10-26','total_minor':'1','paid_minor':'2'}]})
 def test_catalog_has_truthful_calculation_status(self):
  rows={r['operation']:r for r in capabilities()['skills']};self.assertEqual(rows['finance.reconciliation']['status'],'input_analysis_available');self.assertEqual(rows['hr.payroll']['status'],'input_analysis_available');self.assertEqual(rows['hr.tax.pph21']['status'],'not_implemented')
