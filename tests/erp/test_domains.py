import sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'backend'))
from erp_engine.operations import invoke
from erp_engine.planner import ERPError
class Domains(unittest.TestCase):
 def runop(self,op,v):
  r=invoke(op,v);self.assertFalse(r['posted']);self.assertFalse(r['live_database_access']);return r['result']
 def test_monthly_dates_do_not_drift_after_february(self):self.assertEqual(self.runop('isp.billing.cycle',{'start':'2028-01-31','frequency':'monthly','count':3})['dates'],['2028-01-31','2028-02-29','2028-03-31'])
 def test_daily_year_boundary(self):self.assertEqual(self.runop('isp.billing.cycle',{'start':'2026-12-31','frequency':'daily','count':2})['dates'],['2026-12-31','2027-01-01'])
 def test_date_overflow_is_structured(self):
  with self.assertRaisesRegex(ERPError,'date_range_exceeded'):invoke('isp.billing.cycle',{'start':'9999-12-31','frequency':'daily','count':2})
 def test_mrp_finished_stock_reduces_component_demand(self):
  r=self.runop('mfg.mrp',{'bom':[{'item':'A','components':[{'item':'B','quantity':2}]},{'item':'B','components':[{'item':'C','quantity':3}]}],'demand':[{'item':'A','quantity':5}],'stock':[{'item':'A','quantity':1},{'item':'B','quantity':2}]});rows={x['item']:x for x in r['requirements']};self.assertEqual(rows['B']['gross_quantity'],8);self.assertEqual(rows['C']['net_quantity'],18)
 def test_shared_component_aggregates_before_using_stock(self):
  r=self.runop('mfg.bom',{'bom':[{'item':'A','components':[{'item':'C','quantity':2}]},{'item':'B','components':[{'item':'C','quantity':3}]}],'demand':[{'item':'A','quantity':1},{'item':'B','quantity':2}],'stock':[{'item':'C','quantity':5}]});self.assertEqual(next(x for x in r['requirements'] if x['item']=='C')['net_quantity'],3)
 def test_mrp_cycle_and_explosion_are_rejected(self):
  with self.assertRaisesRegex(ERPError,'bom_cycle'):invoke('mfg.mrp',{'bom':[{'item':'A','components':[{'item':'A','quantity':1}]}],'demand':[],'stock':[]})
  with self.assertRaisesRegex(ERPError,'quantity_limit'):invoke('mfg.mrp',{'bom':[{'item':'A','components':[{'item':'B','quantity':1000000}]},{'item':'B','components':[{'item':'C','quantity':1000000}]}],'demand':[{'item':'A','quantity':1000000}],'stock':[]})
 def test_costing_preserves_remainder(self):
  r=self.runop('mfg.production.costing',{'materials':[{'quantity':3,'unit_minor':'100'}],'labor_minor':'1','overhead_minor':'0','units':3});self.assertEqual(r['unit_floor_minor'],'100');self.assertEqual(r['rounding_remainder_minor'],'1')
 def test_opname_only_calculates_adjustment(self):self.assertEqual(self.runop('inventory.stock.opname',{'rows':[{'item':'p','recorded':10,'counted':8}]})['rows'][0]['delta'],-2)
 def test_batch_fefo_excludes_today_expiry(self):
  r=self.runop('inventory.batch.tracking',{'as_of':'2026-10-26','required':5,'batches':[{'id':'expired','expiry':'2026-10-26','quantity':10},{'id':'later','expiry':'2026-12-01','quantity':10},{'id':'sooner','expiry':'2026-11-01','quantity':3}]});self.assertEqual([x['batch_id'] for x in r['allocations']],['sooner','later']);self.assertEqual(r['allocations'][1]['quantity'],2)
 def test_three_way_match_requires_full_lines_currency_and_quantity(self):
  line={'item':'p','quantity':1,'unit_minor':'100','currency':'IDR'}
  self.assertTrue(self.runop('procurement.three.way.match',{k:[line] for k in ['purchase_order','receipt','invoice']})['matched'])
  self.assertFalse(self.runop('procurement.three.way.match',{'purchase_order':[line],'receipt':[line],'invoice':[{**line,'currency':'USD'}]})['matched'])
 def test_capped_discount(self):self.assertEqual(self.runop('retail.promotion',{'subtotal_minor':'1001','discount_bps':5000,'cap_minor':'300'})['net_minor'],'701')
 def test_cash_drawer_variance(self):self.assertEqual(self.runop('pos.cash.management',{'opening_minor':'100','cash_in_minor':'200','cash_out_minor':'50','counted_minor':'240'})['variance_minor'],'-10')
 def test_timezones_and_midnight(self):self.assertEqual(self.runop('pm.time.tracking',{'entries':[{'resource':'tech1','start':'2026-10-26T23:30:00+07:00','end':'2026-10-27T00:30:00+07:00'}]})['total_seconds'],3600)
 def test_overlapping_work_and_missing_timezone_fail(self):
  row={'resource':'x','start':'2026-10-26T10:00:00+07:00','end':'2026-10-26T11:00:00+07:00'}
  with self.assertRaisesRegex(ERPError,'overlapping'):invoke('pm.time.tracking',{'entries':[row,row]})
  with self.assertRaises(ERPError):invoke('pm.time.tracking',{'entries':[{**row,'start':'2026-10-26T10:00:00'}]})

 def test_report_and_workflow_builders_emit_real_sources(self):
  for op in ['builder.gen.report','builder.gen.workflow']:
   r=invoke(op,{'spec':'ISP','company':'QA','industry':'isp'});self.assertFalse(r['deployed']);self.assertIn('apps/api/src/accounting.ts',r['files']);self.assertIn('apps/api/src/isp.ts',r['files'])
 def test_analysis_registry_and_catalog_do_not_drift(self):
  from erp_engine.planner import ANALYSIS_AVAILABLE
  from erp_engine.operations import ANALYSIS_OPS
  self.assertEqual(ANALYSIS_AVAILABLE,ANALYSIS_OPS)

 def test_production_schedule_respects_resource_and_predecessor(self):
  jobs=[{'id':'A','duration_minutes':10,'predecessors':[],'work_center':'w','ready_minute':0},{'id':'B','duration_minutes':5,'predecessors':[],'work_center':'w','ready_minute':0},{'id':'C','duration_minutes':2,'predecessors':['A','B'],'work_center':'x','ready_minute':0}]
  r=self.runop('mfg.production.scheduling',{'jobs':jobs,'work_centers':[{'id':'w','available_minute':3},{'id':'x','available_minute':0}]});self.assertEqual(r['operations'][0]['start_minute'],3);self.assertEqual(r['operations'][1]['start_minute'],13);self.assertEqual(r['operations'][2]['start_minute'],18);self.assertFalse(r['orders_scheduled'])
 def test_routing_finds_dependency_path_and_rejects_cycle(self):
  jobs=[{'id':'A','duration_minutes':5,'predecessors':[]},{'id':'B','duration_minutes':10,'predecessors':['A']}]
  r=self.runop('mfg.routing',{'operations':jobs});self.assertEqual(r['dependency_critical_path'],['A','B']);self.assertEqual(r['makespan_minutes'],15)
  with self.assertRaisesRegex(ERPError,'routing_cycle'):invoke('mfg.routing',{'operations':[{**jobs[0],'predecessors':['B']},jobs[1]]})
 def test_quality_uses_measurements_and_rejected_count(self):
  v={'inspected':10,'accepted':10,'rejected':0,'criteria':[{'id':'width','unit':'mm_x100','value_scaled':1000,'minimum_scaled':990,'maximum_scaled':1010}]};self.assertEqual(self.runop('mfg.quality.control',v)['result'],'PASSED');v['criteria'][0]['value_scaled']=1020;self.assertEqual(self.runop('mfg.quality.control',v)['result'],'FAILED')
 def test_quality_without_criteria_does_not_pass(self):
  with self.assertRaises(ERPError):invoke('mfg.quality.control',{'inspected':1,'accepted':1,'rejected':0,'criteria':[]})
 def test_scrap_preserves_mass_balance(self):
  r=self.runop('mfg.scrap.management',{'issued_quantity':100,'good_quantity':90,'scrap_quantity':5});self.assertEqual(r['remaining_wip_quantity'],5);self.assertEqual(r['good_yield_bps'],9000)
  with self.assertRaisesRegex(ERPError,'mass_balance'):invoke('mfg.scrap.management',{'issued_quantity':100,'good_quantity':90,'scrap_quantity':11})

 def test_schedule_rejects_unhashable_and_unknown_resource(self):
  row={'id':'A','duration_minutes':10,'predecessors':[],'work_center':['bad'],'ready_minute':0}
  with self.assertRaisesRegex(ERPError,'invalid_id'):invoke('mfg.production.scheduling',{'jobs':[row],'work_centers':[]})
  with self.assertRaisesRegex(ERPError,'unknown_work_center'):invoke('mfg.production.scheduling',{'jobs':[{**row,'work_center':'missing'}],'work_centers':[]})
 def test_schedule_horizon_is_bounded(self):
  with self.assertRaisesRegex(ERPError,'schedule_horizon_exceeded'):invoke('mfg.production.scheduling',{'jobs':[{'id':'A','duration_minutes':1,'predecessors':[],'work_center':'w','ready_minute':1000000000}],'work_centers':[{'id':'w','available_minute':0}]})

 def test_cash_receipt_calculation_requires_sufficient_tender(self):
  v={'lines':[{'unit_minor':'100','quantity':3}],'tax_bps':0,'tendered_minor':'500'};r=self.runop('pos.transaction',v);self.assertEqual(r['total_minor'],'300');self.assertEqual(r['change_minor'],'200');self.assertFalse(r['cash_received_verified']);self.assertFalse(r['sale_recorded'])
  with self.assertRaisesRegex(ERPError,'insufficient_cash_tender'):invoke('pos.transaction',{**v,'tendered_minor':'299'})

 def test_crm_pipeline_uses_explicit_probabilities(self):
  r=self.runop('crm.pipeline',{'opportunities':[{'id':'a','stage':'QUALIFIED','value_minor':'1001','probability_bps':5000},{'id':'b','stage':'WON','value_minor':'200','probability_bps':10000}]});self.assertEqual(r['weighted_minor'],'701')
  with self.assertRaisesRegex(ERPError,'inconsistent'):invoke('crm.pipeline',{'opportunities':[{'id':'a','stage':'WON','value_minor':'1','probability_bps':0}]})
 def test_csat_nps_integer_rounding_and_no_agent_guess(self):
  r=self.runop('crm.satisfaction.survey',{'csat':[4,5,3],'nps':[10,9,0]});self.assertEqual(r['csat_bps'],6667);self.assertEqual(r['nps_score'],'33.33');self.assertFalse(r['agent_attribution_inferred'])
  self.assertEqual(self.runop('crm.satisfaction.survey',{'csat':[],'nps':[0,1,10]})['nps_score'],'-33.33')
 def test_project_billing_uses_seconds_and_explicit_rate(self):
  r=self.runop('pm.project.billing',{'entries':[{'id':'time1','seconds':1800,'rate_minor_per_hour':'1001'}],'tax_bps':0});self.assertEqual(r['total_minor'],'501');self.assertFalse(r['invoice_created'])
  with self.assertRaisesRegex(ERPError,'duplicate_time'):invoke('pm.project.billing',{'entries':[{'id':'a','seconds':1,'rate_minor_per_hour':'100'}]*2,'tax_bps':0})
 def test_gantt_svg_escapes_input_and_has_no_script_element(self):
  from xml.etree import ElementTree as ET
  label='<script>alert(1)</script>';r=self.runop('pm.gantt.chart',{'operations':[{'id':label,'duration_minutes':5,'predecessors':[]}]});parsed=ET.fromstring(r['svg']);self.assertEqual(list(parsed.iter('{http://www.w3.org/2000/svg}script')),[]);self.assertIn('&lt;script&gt;',r['svg']);self.assertEqual(r['mime_type'],'image/svg+xml')

 def test_resource_sweep_handles_adjacent_and_overcapacity_intervals(self):
  v={'resources':[{'id':'tech','capacity_units':2}],'assignments':[{'id':'a','resource_id':'tech','start_minute':0,'end_minute':10,'units':2},{'id':'b','resource_id':'tech','start_minute':10,'end_minute':20,'units':2}]};r=self.runop('pm.resource.allocation',v);self.assertTrue(r['feasible']);self.assertEqual(r['resources'][0]['unit_minutes'],'40')
  v['assignments'][1]['start_minute']=5;r=self.runop('pm.resource.allocation',v);self.assertEqual(r['resources'][0]['overallocations'],[{'start_minute':5,'end_minute':10,'units':4,'excess_units':2}]);self.assertFalse(r['assignments_changed'])
  with self.assertRaisesRegex(ERPError,'unknown_resource'):self.runop('pm.resource.allocation',{'resources':[],'assignments':v['assignments']})
 def test_project_risk_expected_exposure_is_explicit_and_bounded(self):
  r=self.runop('pm.risk.management',{'risks':[{'id':'a','probability_bps':5000,'impact_minor':'1001'},{'id':'b','probability_bps':10000,'impact_minor':'900'}]});self.assertEqual(r['risks'][0]['id'],'b');self.assertEqual(r['expected_exposure_sum_minor'],'1401');self.assertFalse(r['probabilities_inferred'])
  with self.assertRaisesRegex(ERPError,'invalid_quantity'):self.runop('pm.risk.management',{'risks':[{'id':'a','probability_bps':10001,'impact_minor':'100'}]})
 def test_portfolio_uses_unrounded_weights_and_rejects_inconsistent_state(self):
  row={'id':'a','status':'ACTIVE','budget_minor':'1','actual_cost_minor':'2','completion_bps':5000};r=self.runop('pm.project.portfolio',{'projects':[row]});self.assertEqual(r['budget_weighted_completion_bps'],5000);self.assertEqual(r['budget_variance_minor'],'-1');self.assertEqual(r['earned_value_minor'],'1');self.assertFalse(r['costs_or_progress_inferred'])
  with self.assertRaisesRegex(ERPError,'completed_progress'):self.runop('pm.project.portfolio',{'projects':[{**row,'status':'COMPLETED'}]})
  self.assertIsNone(self.runop('pm.project.portfolio',{'projects':[]})['budget_weighted_completion_bps'])

 def test_milestone_links_and_overdue_are_not_inferred(self):
  v={'as_of':'2026-10-26','tasks':[{'id':'t','status':'DONE'}],'milestones':[{'id':'m','status':'PENDING','due_on':'2026-10-25','task_ids':['t']}]};r=self.runop('pm.milestone',v);self.assertTrue(r['milestones'][0]['eligible_to_reach']);self.assertTrue(r['milestones'][0]['overdue']);self.assertFalse(r['statuses_changed'])
  with self.assertRaisesRegex(ERPError,'unknown_task'):self.runop('pm.milestone',{**v,'tasks':[]})
  v['tasks'][0]['status']='BLOCKED';v['milestones'][0]['status']='REACHED'
  with self.assertRaisesRegex(ERPError,'reached_tasks_incomplete'):self.runop('pm.milestone',v)
 def test_issue_metrics_require_consistent_dates(self):
  issue={'id':'i','status':'RESOLVED','priority':'HIGH','opened_on':'2026-10-20','resolved_on':'2026-10-22','closed_on':None};r=self.runop('pm.issue.tracking',{'as_of':'2026-10-26','issues':[issue]});self.assertEqual(r['resolution_calendar_day_sum'],2);self.assertTrue(r['issues'][0]['awaiting_owner_closure']);self.assertIsNone(r['oldest_unresolved_calendar_days']);self.assertFalse(r['notifications_sent'])
  with self.assertRaisesRegex(ERPError,'inconsistent_issue_dates'):self.runop('pm.issue.tracking',{'as_of':'2026-10-26','issues':[{**issue,'status':'OPEN'}]})
  with self.assertRaisesRegex(ERPError,'invalid_resolution_date'):self.runop('pm.issue.tracking',{'as_of':'2026-10-26','issues':[{**issue,'resolved_on':'2026-10-19'}]})
