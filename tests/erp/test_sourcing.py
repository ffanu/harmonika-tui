import importlib.util,sys,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'backend'))
from erp_engine.operations import invoke,ANALYSIS_OPS
from erp_engine.planner import ANALYSIS_AVAILABLE,ERPError
SOURCING_OPS=('procurement.rfq','procurement.vendor.performance')
class SourcingAnalysis(unittest.TestCase):
 def runop(self,op,v):
  r=invoke(op,v);self.assertEqual(r['scope'],'caller_supplied_data_only');self.assertFalse(r['posted']);self.assertFalse(r['live_database_access']);self.assertFalse(r['result']['business_data_updated']);return r['result']

 def test_sourcing_operations_registered_consistently(self):
  self.assertTrue(set(SOURCING_OPS)<=ANALYSIS_OPS);self.assertEqual(ANALYSIS_AVAILABLE,ANALYSIS_OPS)
  s=importlib.util.spec_from_file_location('erp_cli_sourcing',ROOT/'runtime/tools/erp_cli.py');m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
  self.assertEqual(set(m.ANALYSIS_OPERATIONS),ANALYSIS_OPS)
  catalog=invoke('capabilities',{});rows={x['operation']:x['status'] for x in catalog['skills']}
  for op in SOURCING_OPS:self.assertEqual(rows[op],'input_analysis_available')

 def rfq(self,**patch):
  v={'as_of':'2026-10-27','currency':'IDR','required':[{'item':'A','quantity':3},{'item':'B','quantity':2}],'quotes':[
   {'vendor':'alpha','submitted_on':'2026-10-20','valid_until':'2026-11-15','tax_bps':1100,'shipping_minor':'0','shipping_taxable':True,'discount_minor':'0','lines':[{'item':'A','unit_minor':'100'},{'item':'B','unit_minor':'50'}]},
   {'vendor':'beta','submitted_on':'2026-10-21','valid_until':'2026-11-20','tax_bps':0,'shipping_minor':'20','shipping_taxable':True,'discount_minor':'10','lines':[{'item':'A','unit_minor':'90'},{'item':'B','unit_minor':'60'}]}]}
  v.update(patch);return v

 def test_rfq_compares_full_basket_totals_with_tax_and_delta(self):
  r=self.runop('procurement.rfq',self.rfq());self.assertEqual([q['vendor'] for q in r['quotes']],['beta','alpha']);self.assertEqual(r['quotes'][0]['total_minor'],'400');self.assertEqual(r['quotes'][1]['subtotal_minor'],'400');self.assertEqual(r['quotes'][1]['net_before_tax_minor'],'400');self.assertEqual(r['quotes'][1]['tax_minor'],'44');self.assertEqual(r['quotes'][1]['total_minor'],'444');self.assertEqual(r['quotes'][1]['delta_to_lowest_minor'],'44');self.assertEqual(r['quotes'][0]['delta_to_lowest_minor'],'0');self.assertEqual(r['lowest_total_minor'],'400');self.assertEqual(r['lowest_total_vendors'],['beta']);self.assertFalse(r['tied_lowest_quotes']);self.assertEqual(r['basket_items'],2);self.assertEqual(r['total_required_units'],5);self.assertFalse(r['fx_conversion_applied']);self.assertFalse(r['supplier_awarded']);self.assertFalse(r['purchase_order_created']);self.assertFalse(r['tax_rate_verified'])

 def test_rfq_tax_rounds_half_up_and_validity_inclusive_of_as_of(self):
  v=self.rfq(quotes=[{'vendor':'solo','submitted_on':'2026-10-27','valid_until':'2026-10-27','tax_bps':1000,'shipping_minor':'0','shipping_taxable':True,'discount_minor':'0','lines':[{'item':'A','unit_minor':'15'}]}],required=[{'item':'A','quantity':3}]);r=self.runop('procurement.rfq',v);self.assertEqual(r['quotes'][0]['tax_minor'],'5');self.assertEqual(r['quotes'][0]['total_minor'],'50');self.assertEqual(r['quotes'][0]['valid_until'],'2026-10-27')

 def test_rfq_tie_lists_all_lowest_vendors_sorted_by_id(self):
  v=self.rfq(required=[{'item':'A','quantity':1}],quotes=[{'vendor':'b-id','submitted_on':'2026-10-20','valid_until':'2026-11-15','tax_bps':0,'shipping_minor':'0','shipping_taxable':True,'discount_minor':'0','lines':[{'item':'A','unit_minor':'10'}]},{'vendor':'a-id','submitted_on':'2026-10-20','valid_until':'2026-11-15','tax_bps':0,'shipping_minor':'0','shipping_taxable':True,'discount_minor':'0','lines':[{'item':'A','unit_minor':'10'}]}]);r=self.runop('procurement.rfq',v);self.assertEqual([q['vendor'] for q in r['quotes']],['a-id','b-id']);self.assertTrue(r['tied_lowest_quotes']);self.assertEqual(r['lowest_total_vendors'],['a-id','b-id'])

 def test_rfq_rejects_basket_missing_extra_duplicate_and_duplicate_vendors(self):
  base=self.rfq()
  with self.assertRaisesRegex(ERPError,'quote_basket_mismatch'):invoke('procurement.rfq',self.rfq(quotes=base['quotes'][:1]+[{**base['quotes'][1],'lines':[{'item':'A','unit_minor':'1'}]}]))
  with self.assertRaisesRegex(ERPError,'quote_basket_mismatch'):invoke('procurement.rfq',self.rfq(quotes=base['quotes'][:1]+[{**base['quotes'][1],'lines':[{'item':'A','unit_minor':'1'},{'item':'B','unit_minor':'1'},{'item':'C','unit_minor':'1'}]}]))
  with self.assertRaisesRegex(ERPError,'duplicate_quote_item'):invoke('procurement.rfq',self.rfq(quotes=base['quotes'][:1]+[{**base['quotes'][1],'lines':[{'item':'A','unit_minor':'1'},{'item':'A','unit_minor':'2'}]}]))
  with self.assertRaisesRegex(ERPError,'duplicate_item'):invoke('procurement.rfq',self.rfq(required=[{'item':'A','quantity':1},{'item':'A','quantity':2}]))
  with self.assertRaisesRegex(ERPError,'duplicate_vendor'):invoke('procurement.rfq',self.rfq(quotes=base['quotes'][:1]+[{**base['quotes'][0],'valid_until':'2026-11-16'}]))
  with self.assertRaisesRegex(ERPError,'basket_required'):invoke('procurement.rfq',self.rfq(required=[]))
  with self.assertRaisesRegex(ERPError,'quotes_required'):invoke('procurement.rfq',self.rfq(quotes=[]))

 def test_rfq_rejects_expired_future_and_calendar_invalid_dates(self):
  base=self.rfq()
  with self.assertRaisesRegex(ERPError,'expired_quote_validity'):invoke('procurement.rfq',self.rfq(quotes=[{**base['quotes'][0],'valid_until':'2026-10-26'}]))
  with self.assertRaisesRegex(ERPError,'future_submission_date'):invoke('procurement.rfq',self.rfq(quotes=[{**base['quotes'][0],'submitted_on':'2026-10-28'}]))
  with self.assertRaisesRegex(ERPError,'invalid_validity_window'):invoke('procurement.rfq',self.rfq(quotes=[{**base['quotes'][0],'submitted_on':'2026-10-26','valid_until':'2026-10-25'}]))
  for bad in ('2026-02-30','2027-02-29','2026-10-2',True,20261027):
   with self.assertRaisesRegex(ERPError,'invalid_date'):invoke('procurement.rfq',self.rfq(quotes=[{**base['quotes'][0],'valid_until':bad}]))
  self.runop('procurement.rfq',self.rfq(quotes=[{**base['quotes'][0],'valid_until':'2028-02-29'}]))

 def test_rfq_rejects_typed_money_bool_quantities_currency_and_bounds(self):
  base=self.rfq()
  with self.assertRaisesRegex(ERPError,'invalid_money'):invoke('procurement.rfq',self.rfq(quotes=[{**base['quotes'][0],'lines':[{'item':'A','unit_minor':100},{'item':'B','unit_minor':'1'}]}]))
  with self.assertRaisesRegex(ERPError,'invalid_money'):invoke('procurement.rfq',self.rfq(quotes=[{**base['quotes'][0],'discount_minor':'00'}]))
  for bad in (10001,-1,True):
   with self.assertRaisesRegex(ERPError,'invalid_quantity'):invoke('procurement.rfq',self.rfq(quotes=[{**base['quotes'][0],'tax_bps':bad}]))
  with self.assertRaisesRegex(ERPError,'invalid_quantity'):invoke('procurement.rfq',self.rfq(required=[{'item':'A','quantity':0}]))
  for bad in ('idr','ID',123,None):
   with self.assertRaisesRegex(ERPError,'invalid_currency'):invoke('procurement.rfq',self.rfq(currency=bad))
  with self.assertRaisesRegex(ERPError,'discount_exceeds_subtotal'):invoke('procurement.rfq',self.rfq(quotes=[{**base['quotes'][0],'discount_minor':'401'}]))
  with self.assertRaisesRegex(ERPError,'amount_exceeds_storage_limit'):invoke('procurement.rfq',self.rfq(required=[{'item':'A','quantity':1000000}],quotes=[{**base['quotes'][0],'lines':[{'item':'A','unit_minor':'999999999999999999'}]}]))

 def test_rfq_rejects_row_budgets_and_unknown_fields(self):
  with self.assertRaisesRegex(ERPError,'invalid_rows'):invoke('procurement.rfq',self.rfq(required=[{'item':'i%d'%n,'quantity':1} for n in range(201)]))
  with self.assertRaisesRegex(ERPError,'invalid_rows'):invoke('procurement.rfq',self.rfq(required=[{'item':'A','quantity':1}],quotes=[{**self.rfq()['quotes'][0],'lines':[{'item':'i%d'%n,'unit_minor':'1'} for n in range(201)]}]))
  with self.assertRaisesRegex(ERPError,'invalid_rows'):invoke('procurement.rfq',self.rfq(required=[{'item':'A','quantity':1}],quotes=[{**self.rfq()['quotes'][0],'vendor':'v%d'%n} for n in range(101)]))
  with self.assertRaisesRegex(ERPError,'invalid_fields'):invoke('procurement.rfq',self.rfq(memo='x'))
  with self.assertRaisesRegex(ERPError,'invalid_fields'):invoke('procurement.rfq',self.rfq(quotes=[{**self.rfq()['quotes'][0],'fx_rate':1}]))
  with self.assertRaisesRegex(ERPError,'invalid_fields'):invoke('procurement.rfq',self.rfq(required=[{'item':'A','quantity':1,'note':'x'}]))

 def performance(self,**patch):
  v={'as_of':'2026-10-27','weights':[{'metric':'on_time_delivery','weight_bps':4000},{'metric':'quantity_accuracy','weight_bps':3000},{'metric':'quality_acceptance','weight_bps':3000}],'records':[
   {'id':'a1','vendor':'alpha','ordered_on':'2026-09-01','promised_on':'2026-10-10','delivered_on':'2026-10-09','ordered_quantity':10,'delivered_quantity':10,'accepted_quantity':9,'rejected_quantity':1},
   {'id':'a2','vendor':'alpha','ordered_on':'2026-09-01','promised_on':'2026-10-10','delivered_on':'2026-10-12','ordered_quantity':10,'delivered_quantity':9,'accepted_quantity':9,'rejected_quantity':0},
   {'id':'b1','vendor':'beta','ordered_on':'2026-09-01','promised_on':'2026-10-10','delivered_on':'2026-10-10','ordered_quantity':5,'delivered_quantity':5,'accepted_quantity':5,'rejected_quantity':0},
   {'id':'c1','vendor':'gamma','ordered_on':'2026-10-01','promised_on':'2026-11-01','delivered_on':None,'ordered_quantity':7,'delivered_quantity':0,'accepted_quantity':0,'rejected_quantity':0}]}
  v.update(patch);return v

 def test_vendor_performance_scores_explicit_metrics_and_weights(self):
  r=self.runop('procurement.vendor.performance',self.performance());self.assertEqual([x['vendor'] for x in r['vendors']],['beta','alpha']);alpha=r['vendors'][1]
  self.assertEqual(alpha['delivered_samples'],2);self.assertEqual(alpha['on_time_samples'],1);self.assertEqual(alpha['exact_quantity_samples'],1);self.assertEqual(alpha['delivered_units'],19);self.assertEqual(alpha['accepted_units'],18)
  self.assertEqual(alpha['on_time_delivery_bps'],5000);self.assertEqual(alpha['quantity_accuracy_bps'],5000);self.assertEqual(alpha['quality_acceptance_bps'],9474);self.assertEqual(alpha['score_bps'],6342)
  self.assertEqual(r['vendors'][0]['score_bps'],10000);self.assertEqual(r['top_score_bps'],10000);self.assertEqual(r['top_vendors'],['beta']);self.assertFalse(r['tied_top_vendors'])
  self.assertEqual(r['unscored_vendors'],[{'vendor':'gamma','order_count':1,'delivered_samples':0,'score_bps':None,'reason':'zero_delivered_samples','open_orders':1,'overdue_open_orders':0,'delivery_sample_coverage_bps':0}])
  self.assertEqual(r['weights'],{'on_time_delivery':4000,'quality_acceptance':3000,'quantity_accuracy':3000});self.assertEqual(r['weights_source'],'caller_supplied');self.assertFalse(r['quality_inferred']);self.assertFalse(r['rating_labels_inferred']);self.assertFalse(r['sanctions_applied']);self.assertFalse(r['vendor_records_changed'])

 def test_vendor_performance_tied_top_vendors_are_all_listed(self):
  perfect=lambda key:{'id':key,'vendor':key,'ordered_on':'2026-09-01','promised_on':'2026-10-10','delivered_on':'2026-10-10','ordered_quantity':2,'delivered_quantity':2,'accepted_quantity':2,'rejected_quantity':0}
  r=self.runop('procurement.vendor.performance',self.performance(records=[perfect('v2'),perfect('v1')]));self.assertTrue(r['tied_top_vendors']);self.assertEqual(r['top_vendors'],['v1','v2']);self.assertEqual([x['vendor'] for x in r['vendors']],['v1','v2'])

 def test_vendor_performance_never_rates_zero_sample_vendors(self):
  r=self.runop('procurement.vendor.performance',self.performance(records=[self.performance()['records'][3]]));self.assertEqual(r['vendors'],[]);self.assertIsNone(r['top_score_bps']);self.assertEqual(r['top_vendors'],[]);self.assertFalse(r['tied_top_vendors']);self.assertEqual([x['vendor'] for x in r['unscored_vendors']],['gamma'])
  self.runop('procurement.vendor.performance',self.performance(records=[]))

 def test_vendor_performance_requires_full_explicit_weights(self):
  for bad,code in [(lambda w:w[:2],'weights_must_sum_to_10000'),(lambda w:[w[0],w[0],w[2]],'duplicate_metric'),(lambda w:[{**w[0],'metric':'price'},w[1],w[2]],'invalid_metric'),(lambda w:[{**w[0],'weight_bps':5000},w[1],w[2]],'weights_must_sum_to_10000'),(lambda w:[{**w[0],'weight_bps':True},w[1],w[2]],'invalid_quantity')]:
   v=self.performance();v['weights']=bad(v['weights'])
   with self.assertRaisesRegex(ERPError,code):invoke('procurement.vendor.performance',v)
  with self.assertRaisesRegex(ERPError,'invalid_rows'):invoke('procurement.vendor.performance',self.performance(weights='balanced'))

 def test_vendor_performance_rejects_inconsistent_dates_and_mass_balance(self):
  base=self.performance()['records'][3]
  for patch,code in [({'delivered_on':'2026-10-28','delivered_quantity':7,'accepted_quantity':7,'rejected_quantity':0},'invalid_delivery_date'),({'delivered_on':'2026-08-31','delivered_quantity':7,'accepted_quantity':7,'rejected_quantity':0},'invalid_delivery_date'),({'ordered_on':'2026-10-28'},'future_order_date'),({'promised_on':'2026-08-31'},'invalid_promised_date'),({'delivered_on':'2026-10-05','delivered_quantity':7,'accepted_quantity':6,'rejected_quantity':2},'invalid_inspection_balance'),({'delivered_on':'2026-10-05','delivered_quantity':0,'accepted_quantity':0,'rejected_quantity':0},'invalid_inspection_balance'),({'delivered_quantity':1,'accepted_quantity':1,'rejected_quantity':0},'invalid_open_order'),({'delivered_on':'2026-10-2','delivered_quantity':7,'accepted_quantity':7,'rejected_quantity':0},'invalid_date'),({'delivered_on':True,'delivered_quantity':7,'accepted_quantity':7,'rejected_quantity':0},'invalid_date')]:
   with self.assertRaisesRegex(ERPError,code):invoke('procurement.vendor.performance',self.performance(records=[{**base,**patch}]))
  with self.assertRaisesRegex(ERPError,'duplicate_record'):invoke('procurement.vendor.performance',self.performance(records=[self.performance()['records'][0],self.performance()['records'][0]]))

 def test_vendor_performance_rejects_typed_quantities_and_unknown_fields(self):
  base=self.performance()['records'][3]
  for field,bad in [('ordered_quantity','7'),('delivered_quantity',1.5),('accepted_quantity',True),('rejected_quantity',None)]:
   with self.assertRaisesRegex(ERPError,'invalid_quantity'):invoke('procurement.vendor.performance',self.performance(records=[{**base,field:bad}]))
  with self.assertRaisesRegex(ERPError,'invalid_fields'):invoke('procurement.vendor.performance',self.performance(records=[{**base,'quality':'good'}]))
  with self.assertRaisesRegex(ERPError,'invalid_fields'):invoke('procurement.vendor.performance',self.performance(grade_curve=True))

 def test_large_sourcing_response_is_rejected_explicitly(self):
  record=lambda n:{'id':'r%04d'%n,'vendor':'v%03d'+str(n)+'x'*90,'ordered_on':'2026-09-01','promised_on':'2026-10-10','delivered_on':'2026-10-10','ordered_quantity':1,'delivered_quantity':1,'accepted_quantity':1,'rejected_quantity':0}
  with self.assertRaisesRegex(ERPError,'sourcing_result_limit'):invoke('procurement.vendor.performance',self.performance(records=[record(n) for n in range(1000)]))



 def test_shipping_taxability_must_be_explicit(self):
  v=self.rfq(required=[{'item':'A','quantity':1}],quotes=[{'vendor':'v','submitted_on':'2026-10-01','valid_until':'2026-11-01','tax_bps':1000,'shipping_minor':'100','shipping_taxable':False,'discount_minor':'0','lines':[{'item':'A','unit_minor':'100'}]}]);r=self.runop('procurement.rfq',v);self.assertEqual(r['quotes'][0]['total_minor'],'210');self.assertEqual(r['quotes'][0]['tax_base_minor'],'100')
  v['quotes'][0]['shipping_taxable']=True;self.assertEqual(self.runop('procurement.rfq',v)['quotes'][0]['total_minor'],'220')
  v['quotes'][0]['shipping_taxable']='yes'
  with self.assertRaisesRegex(ERPError,'explicit_shipping_tax_policy_required'):invoke('procurement.rfq',v)
 def test_overdue_open_orders_remain_visible_beside_delivered_sample_score(self):
  v=self.performance();v['records']=[v['records'][2],{**v['records'][3],'vendor':'beta','promised_on':'2026-10-10'}];r=self.runop('procurement.vendor.performance',v);x=r['vendors'][0];self.assertEqual(x['score_bps'],10000);self.assertEqual(x['overdue_open_orders'],1);self.assertEqual(x['delivery_sample_coverage_bps'],5000);self.assertFalse(r['source_evidence_verified'])
