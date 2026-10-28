import sys,unittest,copy
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'backend'))
from erp_engine.operations import invoke
from erp_engine.planner import ERPError
class Reporting(unittest.TestCase):
 def fixture(self):
  accounts=[{'code':code,'type':kind,'is_cash':cash} for code,kind,cash in [('cash','asset',True),('bank','asset',True),('equipment','asset',False),('capital','equity',False),('revenue','revenue',False),('expense','expense',False)]]
  def j(key,period,category,debit,credit,amount):return {'id':key,'period':period,'status':'POSTED','cashflow_category':category,'lines':[{'account_code':debit,'debit_minor':amount,'credit_minor':'0'},{'account_code':credit,'debit_minor':'0','credit_minor':amount}]}
  rows=[j('capital','2025-12','financing','cash','capital','1000'),j('sales','2026-01','operating','cash','revenue','200'),j('rent','2026-01','operating','expense','cash','50'),j('purchase','2026-01','investing','equipment','cash','200'),j('transfer','2026-01','internal_transfer','bank','cash','100'),j('future','2026-02','operating','cash','revenue','100')]
  return {'period':'2026-01','currency':'IDR','accounts':accounts,'journals':rows}
 def calc(self,op='finance.financial.statement',data=None):return invoke(op,data or self.fixture())['result']
 def test_posted_period_and_accumulated_balances_reconcile(self):
  r=self.calc();self.assertEqual(r['balance_sheet']['assets_minor'],'1150');self.assertEqual(r['balance_sheet']['imbalance_minor'],'0');self.assertEqual(r['profit_loss']['profit_minor'],'150');self.assertEqual(r['journal_count_included'],5);self.assertFalse(r['ledger_posted']);self.assertFalse(r['compliance_certified'])
 def test_cash_opening_categories_and_internal_transfer(self):
  r=self.calc('finance.cashflow.statement')['cash_flow'];self.assertEqual(r['opening_minor'],'1000');self.assertEqual(r['categories_minor'],{'operating':'150','investing':'-200','financing':'0','unclassified':'0'});self.assertEqual(r['net_minor'],'-50');self.assertEqual(r['closing_minor'],'950');self.assertEqual(r['reconciliation_difference_minor'],'0')
 def test_report_views_share_totals_but_only_return_requested_sections(self):
  full=self.calc('report.financial.statement');profit=self.calc('report.profit.loss');cash=self.calc('report.cash.flow');self.assertEqual(full['profit_loss'],profit['profit_loss']);self.assertEqual(full['cash_flow'],cash['cash_flow']);self.assertNotIn('balance_sheet',profit);self.assertNotIn('profit_loss',cash)
 def test_unbalanced_unknown_and_draft_input_rejected(self):
  for change,code in [('unbalanced','unbalanced_journal'),('unknown','unknown_account'),('draft','posted_journals_only')]:
   v=self.fixture()
   if change=='unbalanced':v['journals'][0]['lines'][0]['debit_minor']='1001'
   elif change=='unknown':v['journals'][0]['lines'][0]['account_code']='missing'
   else:v['journals'][0]['status']='DRAFT'
   with self.assertRaisesRegex(ERPError,code):self.calc(data=v)
 def test_unclassified_cash_that_nets_zero_is_still_flagged(self):
  v=self.fixture();v['journals']=v['journals'][1:3];v['journals'][1]['lines'][0]['debit_minor']='200';v['journals'][1]['lines'][1]['credit_minor']='200'
  for j in v['journals']:j['cashflow_category']='unclassified'
  r=self.calc(data=v)['cash_flow'];self.assertEqual(r['categories_minor']['unclassified'],'0');self.assertTrue(r['classification_required']);self.assertEqual(r['unclassified_cash_journals'],2)
 def test_duplicate_ids_and_non_cash_internal_transfer_rejected(self):
  v=self.fixture();v['journals'].append(copy.deepcopy(v['journals'][0]))
  with self.assertRaisesRegex(ERPError,'duplicate_journal'):self.calc(data=v)
  v=self.fixture();v['journals'][1]['cashflow_category']='internal_transfer'
  with self.assertRaisesRegex(ERPError,'invalid_internal_transfer'):self.calc(data=v)
 def test_cash_account_must_be_explicit_asset_and_real_month(self):
  v=self.fixture();v['accounts'][-1]['is_cash']=True
  with self.assertRaisesRegex(ERPError,'invalid_cash_account'):self.calc(data=v)
  v=self.fixture();v['period']='2026-13'
  with self.assertRaisesRegex(ERPError,'invalid_period'):self.calc(data=v)
 def test_reversal_stays_visible_in_later_period(self):
  v=self.fixture();reverse=copy.deepcopy(v['journals'][1]);reverse.update(id='reversal',period='2026-02');reverse['lines']=[{'account_code':l['account_code'],'debit_minor':l['credit_minor'],'credit_minor':l['debit_minor']} for l in reverse['lines']];v['journals'].append(reverse);v['period']='2026-02';r=self.calc(data=v);self.assertEqual(r['profit_loss']['profit_minor'],'-100');self.assertEqual(r['balance_sheet']['accumulated_profit_minor'],'50');self.assertEqual(r['balance_sheet']['imbalance_minor'],'0')

 def test_large_money_is_exact_beyond_float_precision(self):
  v=self.fixture()
  for line in v['journals'][0]['lines']:
   side='debit_minor' if line['debit_minor']!='0' else 'credit_minor';line[side]='100000000000000001'
  r=self.calc(data=v);self.assertEqual(r['balance_sheet']['assets_minor'],'100000000000000151');self.assertEqual(r['cash_flow']['closing_minor'],'99999999999999951');self.assertEqual(r['balance_sheet']['imbalance_minor'],'0')
 def test_aggregated_money_cannot_exceed_storage_bound(self):
  v=self.fixture();base=v['journals'][0];v['journals']=[]
  for n in range(10):
   row=copy.deepcopy(base);row['id']=str(n)
   for line in row['lines']:
    side='debit_minor' if line['debit_minor']!='0' else 'credit_minor';line[side]='999999999999999999'
   v['journals'].append(row)
  with self.assertRaisesRegex(ERPError,'amount_exceeds_storage_limit'):self.calc(data=v)

 def test_net_zero_unclassified_transfer_still_requires_label(self):
  v=self.fixture();v['journals']=[v['journals'][0],v['journals'][4]];v['journals'][1]['cashflow_category']='unclassified';r=self.calc(data=v)['cash_flow'];self.assertEqual(r['categories_minor']['unclassified'],'0');self.assertTrue(r['classification_required']);self.assertEqual(r['unclassified_cash_journals'],1)
  v['journals'][1]['cashflow_category']='internal_transfer';self.assertFalse(self.calc(data=v)['cash_flow']['classification_required'])
 def test_empty_activity_is_not_claimed_as_verified_books(self):
  v=self.fixture();v['journals']=[];r=self.calc(data=v);self.assertEqual(r['data_status'],'no_posted_activity_supplied');self.assertFalse(r['source_completeness_verified']);self.assertFalse(r['bank_reconciled']);self.assertTrue(r['equation_self_checks_only'])
 def test_all_supplied_lines_count_against_processing_limit(self):
  v=self.fixture();first={'account_code':'cash','debit_minor':'1','credit_minor':'0'};second={'account_code':'capital','debit_minor':'0','credit_minor':'1'};v['journals']=[{'id':str(n),'period':'2025-12','status':'POSTED','cashflow_category':'financing','lines':[first]*50+[second]*50} for n in range(100)];self.calc(data=v)
  v['journals'].append({'id':'future','period':'2026-02','status':'POSTED','cashflow_category':'financing','lines':[first,second]})
  with self.assertRaisesRegex(ERPError,'report_line_limit'):self.calc(data=v)
 def test_typed_mutation_and_unknown_fields_never_become_success(self):
  for mutate,code in [(lambda v:v['journals'][0].update(status=True),'posted_journals_only'),(lambda v:v['journals'][0]['lines'][0].update(debit_minor=1000),'invalid_money'),(lambda v:v['journals'][0]['lines'][0].update(debit_minor='9999999999999999999'),'invalid_money'),(lambda v:v['journals'][0].update(memo='x'),'invalid_fields'),(lambda v:v['journals'][0]['lines'][0].update(credit_minor='1000'),'invalid_journal_line')]:
   v=self.fixture();mutate(v)
   with self.assertRaisesRegex(ERPError,code):self.calc(data=v)
