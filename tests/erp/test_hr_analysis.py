import importlib.util,sys,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'backend'))
from erp_engine.operations import invoke,ANALYSIS_OPS
from erp_engine.planner import ANALYSIS_AVAILABLE,ERPError
HR_OPS=('hr.attendance','hr.leave.management','hr.payroll','hr.time.overtime','hr.expense.claim','hr.loan.management')
class HRAnalysis(unittest.TestCase):
 def runop(self,op,v):
  r=invoke(op,v);self.assertEqual(r['scope'],'caller_supplied_data_only');self.assertFalse(r['posted']);self.assertFalse(r['live_database_access']);self.assertFalse(r['result']['business_data_updated']);self.assertFalse(r['result']['disbursement_sent']);self.assertFalse(r['result']['compliance_verified']);return r['result']

 def test_hr_operations_registered_consistently(self):
  self.assertTrue(set(HR_OPS)<=ANALYSIS_OPS);self.assertEqual(ANALYSIS_AVAILABLE,ANALYSIS_OPS)
  s=importlib.util.spec_from_file_location('erp_cli_hr',ROOT/'runtime/tools/erp_cli.py');m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
  self.assertEqual(set(m.ANALYSIS_OPERATIONS),ANALYSIS_OPS)
  catalog=invoke('capabilities',{});rows={x['operation']:x['status'] for x in catalog['skills']}
  for op in HR_OPS:self.assertEqual(rows[op],'input_analysis_available')

 def test_attendance_totals_adjacent_intervals_and_cross_midnight(self):
  v={'punches':[{'id':'p1','employee':'e2','check_in':'2026-10-26T23:30:00+07:00','check_out':'2026-10-27T00:30:00+07:00'},{'id':'p2','employee':'e1','check_in':'2026-10-26T09:00:00+07:00','check_out':'2026-10-26T10:00:00+07:00'},{'id':'p3','employee':'e1','check_in':'2026-10-26T10:00:00+07:00','check_out':'2026-10-26T11:30:00+07:00'}]}
  r=self.runop('hr.attendance',v);self.assertEqual([x['employee'] for x in r['employees']],['e1','e2']);self.assertEqual(r['employees'][0]['seconds'],9000);self.assertEqual(r['employees'][1]['seconds'],3600);self.assertEqual(r['total_punches'],3);self.assertEqual(r['total_seconds'],12600);self.assertFalse(r['attendance_records_written']);self.assertFalse(r['payroll_inferred'])

 def test_attendance_rejects_overlap_per_employee_not_across_employees(self):
  base={'employee':'e1','check_in':'2026-10-26T09:00:00+07:00','check_out':'2026-10-26T10:00:00+07:00'}
  other={**base,'employee':'e2','id':'q1'};self.runop('hr.attendance',{'punches':[{**base,'id':'a'},other]})
  with self.assertRaisesRegex(ERPError,'overlapping_attendance'):invoke('hr.attendance',{'punches':[{**base,'id':'a'},{**base,'id':'b','check_in':'2026-10-26T09:30:00+07:00','check_out':'2026-10-26T10:30:00+07:00'}]})

 def test_attendance_timestamps_must_be_real_calendar_tz_aware_whole_seconds(self):
  base={'id':'a','employee':'e1','check_in':'2026-10-26T09:00:00+07:00','check_out':'2026-10-26T10:00:00+07:00'}
  for check_in,code in [('2026-02-30T09:00:00+07:00','invalid_timestamp'),('2026-02-29T09:00:00+07:00','invalid_timestamp'),('2026-10-26T09:00:00','timezone_and_whole_seconds_required'),('2026-10-26T09:00:00.500000+07:00','timezone_and_whole_seconds_required'),(True,'invalid_timestamp')]:
   with self.assertRaisesRegex(ERPError,code):invoke('hr.attendance',{'punches':[{**base,'check_in':check_in}]})
  with self.assertRaisesRegex(ERPError,'invalid_duration'):invoke('hr.attendance',{'punches':[{**base,'check_out':base['check_in']}]})
  with self.assertRaisesRegex(ERPError,'invalid_duration'):invoke('hr.attendance',{'punches':[{**base,'check_out':'2026-10-28T09:00:01+07:00'}]})
  with self.assertRaisesRegex(ERPError,'duplicate_punch'):invoke('hr.attendance',{'punches':[base,base]})

 def test_leave_balances_and_sequential_requests(self):
  v={'balances':[{'employee':'e1','leave_type':'annual','granted_units':12,'taken_units':5}],'requests':[{'id':'r1','employee':'e1','leave_type':'annual','units':3},{'id':'r2','employee':'e1','leave_type':'annual','units':4}]}
  r=self.runop('hr.leave.management',v);self.assertEqual(r['balances'][0]['remaining_units'],7);self.assertEqual(r['requests'][0]['remaining_units_after'],4);self.assertEqual(r['requests'][1]['remaining_units_after'],0);self.assertEqual(r['total_requested_units'],7);self.assertFalse(r['leave_records_changed']);self.assertFalse(r['approvals_recorded'])

 def test_leave_rejects_overdraw_duplicates_unknown_and_bool_units(self):
  with self.assertRaisesRegex(ERPError,'leave_overdrawn'):invoke('hr.leave.management',{'balances':[{'employee':'e1','leave_type':'annual','granted_units':5,'taken_units':6}],'requests':[]})
  v={'balances':[{'employee':'e1','leave_type':'annual','granted_units':5,'taken_units':0}],'requests':[{'id':'r1','employee':'e1','leave_type':'annual','units':6}]}
  with self.assertRaisesRegex(ERPError,'leave_overdrawn'):invoke('hr.leave.management',v)
  v['requests'][0]['units']=1
  with self.assertRaisesRegex(ERPError,'unknown_leave_balance'):invoke('hr.leave.management',{**v,'requests':[{**v['requests'][0],'leave_type':'sick'}]})
  with self.assertRaisesRegex(ERPError,'duplicate_balance'):invoke('hr.leave.management',{'balances':[{**v['balances'][0]},{**v['balances'][0]}],'requests':[]})
  with self.assertRaisesRegex(ERPError,'duplicate_request'):invoke('hr.leave.management',{**v,'requests':[v['requests'][0],{**v['requests'][0],'units':1}]})
  for bad in (True,1.5,'2'):
   with self.assertRaisesRegex(ERPError,'invalid_quantity'):invoke('hr.leave.management',{**v,'requests':[{**v['requests'][0],'units':bad}]})

 def test_payroll_sums_explicit_earnings_and_deductions(self):
  v={'employees':[{'id':'e1','earnings':[{'component':'base','amount_minor':'5000000'},{'component':'transport','amount_minor':'500000'}],'deductions':[{'component':'loan','amount_minor':'300000'},{'component':'other','amount_minor':'200000'}]},{'id':'e2','earnings':[{'component':'base','amount_minor':'1000'}],'deductions':[]}]}
  r=self.runop('hr.payroll',v);self.assertEqual(r['employees'][0]['gross_minor'],'5500000');self.assertEqual(r['employees'][0]['net_minor'],'5000000');self.assertEqual(r['employees'][1]['net_minor'],'1000');self.assertEqual(r['gross_minor'],'5501000');self.assertEqual(r['net_minor'],'5001000');self.assertFalse(r['tax_or_statutory_deductions_inferred']);self.assertFalse(r['payslips_issued']);self.assertFalse(r['payroll_disbursed']);self.assertFalse(r['compliance_verified'])

 def test_payroll_rejects_over_deduction_duplicates_typed_and_overflow(self):
  over={'id':'e1','earnings':[{'component':'base','amount_minor':'50'}],'deductions':[{'component':'loan','amount_minor':'100'}]}
  with self.assertRaisesRegex(ERPError,'deductions_exceed_gross'):invoke('hr.payroll',{'employees':[over]})
  base={'id':'e1','earnings':[{'component':'base','amount_minor':'50'}],'deductions':[{'component':'loan','amount_minor':'10'}]}
  with self.assertRaisesRegex(ERPError,'duplicate_employee'):invoke('hr.payroll',{'employees':[base,{**base,'deductions':[]}]})
  with self.assertRaisesRegex(ERPError,'duplicate_component'):invoke('hr.payroll',{'employees':[{**base,'deductions':[],'earnings':[{'component':'base','amount_minor':'1'},{'component':'base','amount_minor':'2'}]}]})
  for bad in (100,True,None):
   with self.assertRaisesRegex(ERPError,'invalid_money'):invoke('hr.payroll',{'employees':[{**base,'deductions':[],'earnings':[{'component':'base','amount_minor':bad}]}]})
  with self.assertRaisesRegex(ERPError,'invalid_money'):invoke('hr.payroll',{'employees':[{**base,'deductions':[],'earnings':[{'component':'base','amount_minor':'9999999999999999999'}]}]})
  big={'id':'e1','earnings':[{'component':'c%d'%n,'amount_minor':'999999999999999999'} for n in range(100)],'deductions':[]}
  with self.assertRaisesRegex(ERPError,'amount_exceeds_storage_limit'):invoke('hr.payroll',{'employees':[big]})

 def test_overtime_uses_supplied_threshold_and_rate_half_up(self):
  v={'days':[{'id':'d1','employee':'e1','date':'2026-10-26','regular_minutes':480,'worked_minutes':600,'overtime_rate_minor_per_hour':'1001'},{'id':'d2','employee':'e1','date':'2026-10-27','regular_minutes':480,'worked_minutes':300,'overtime_rate_minor_per_hour':'999999999999999999'},{'id':'d3','employee':'e2','date':'2026-10-26','regular_minutes':0,'worked_minutes':1,'overtime_rate_minor_per_hour':'30'}]}
  r=self.runop('hr.time.overtime',v);self.assertEqual(r['rows'][0]['overtime_minutes'],120);self.assertEqual(r['rows'][0]['amount_minor'],'2002');self.assertEqual(r['rows'][1]['overtime_minutes'],0);self.assertEqual(r['rows'][1]['amount_minor'],'0');self.assertEqual(r['rows'][2]['amount_minor'],'1');self.assertEqual(r['total_overtime_minutes'],121);self.assertEqual(r['total_amount_minor'],'2003');self.assertFalse(r['statutory_overtime_rules_applied']);self.assertFalse(r['payroll_posted']);self.assertEqual(r['regular_threshold_source'],'caller_supplied_per_day_not_statutory')

 def test_overtime_rejects_duplicate_ids_days_dates_and_overflow(self):
  base={'id':'d1','employee':'e1','date':'2026-10-26','regular_minutes':480,'worked_minutes':600,'overtime_rate_minor_per_hour':'100'}
  with self.assertRaisesRegex(ERPError,'duplicate_day'):invoke('hr.time.overtime',{'days':[base,base]})
  with self.assertRaisesRegex(ERPError,'duplicate_employee_day'):invoke('hr.time.overtime',{'days':[base,{**base,'id':'d2'}]})
  for bad in ('2026-02-29','2026-13-01','2026-2-3',True,20261026):
   with self.assertRaisesRegex(ERPError,'invalid_date'):invoke('hr.time.overtime',{'days':[{**base,'date':bad}]})
  self.runop('hr.time.overtime',{'days':[{**base,'date':'2028-02-29'}]})
  with self.assertRaisesRegex(ERPError,'invalid_quantity'):invoke('hr.time.overtime',{'days':[{**base,'worked_minutes':1441}]})
  for bad in (True,'480',1.0):
   with self.assertRaisesRegex(ERPError,'invalid_quantity'):invoke('hr.time.overtime',{'days':[{**base,'regular_minutes':bad}]})
  with self.assertRaisesRegex(ERPError,'invalid_money'):invoke('hr.time.overtime',{'days':[{**base,'overtime_rate_minor_per_hour':100}]})
  with self.assertRaisesRegex(ERPError,'amount_exceeds_storage_limit'):invoke('hr.time.overtime',{'days':[{**base,'regular_minutes':0,'worked_minutes':1440,'overtime_rate_minor_per_hour':'999999999999999999'}]})

 def test_expense_claims_validate_declared_policy_caps(self):
  v={'policy':[{'category':'travel','cap_minor':'1000'},{'category':'meal','cap_minor':'200'}],'claims':[{'id':'c1','employee':'e1','category':'travel','amount_minor':'800'},{'id':'c2','employee':'e1','category':'travel','amount_minor':'1200'},{'id':'c3','employee':'e2','category':'meal','amount_minor':'200'}]}
  r=self.runop('hr.expense.claim',v);self.assertTrue(r['rows'][0]['within_policy']);self.assertFalse(r['rows'][1]['within_policy']);self.assertEqual(r['rows'][1]['excess_minor'],'200');self.assertTrue(r['rows'][2]['within_policy']);self.assertEqual(r['claimed_minor'],'2200');self.assertEqual(r['within_policy_minor'],'2000');self.assertEqual(r['over_policy_minor'],'200');self.assertFalse(r['reimbursement_paid']);self.assertFalse(r['claims_recorded']);self.assertFalse(r['compliance_verified']);self.assertEqual(r['policy_source'],'caller_supplied_not_statutory')

 def test_expense_rejects_unknown_category_duplicates_and_empty_policy(self):
  policy=[{'category':'travel','cap_minor':'100'}];claim={'id':'c1','employee':'e1','category':'travel','amount_minor':'10'}
  with self.assertRaisesRegex(ERPError,'unknown_expense_category'):invoke('hr.expense.claim',{'policy':policy,'claims':[{**claim,'category':'meal'}]})
  with self.assertRaisesRegex(ERPError,'duplicate_claim'):invoke('hr.expense.claim',{'policy':policy,'claims':[claim,claim]})
  with self.assertRaisesRegex(ERPError,'duplicate_policy_category'):invoke('hr.expense.claim',{'policy':policy+policy,'claims':[]})
  with self.assertRaisesRegex(ERPError,'policy_required'):invoke('hr.expense.claim',{'policy':[],'claims':[]})
  with self.assertRaisesRegex(ERPError,'invalid_money'):invoke('hr.expense.claim',{'policy':policy,'claims':[{**claim,'amount_minor':True}]})
  with self.assertRaisesRegex(ERPError,'amount_exceeds_storage_limit'):invoke('hr.expense.claim',{'policy':policy,'claims':[{**claim,'id':'c%d'%n,'amount_minor':'999999999999999999'} for n in range(1000)]})

 def test_loan_reconciles_supplied_payments_and_allocates_exact_remainder(self):
  v={'loans':[{'id':'l1','employee':'e1','principal_minor':'1000','charges_minor':'1','payments':[{'id':'p1','amount_minor':'100'},{'id':'p2','amount_minor':'500'}],'installments':3},{'id':'l2','employee':'e2','principal_minor':'500','charges_minor':'0','payments':[{'id':'p3','amount_minor':'500'}],'installments':0}]}
  r=self.runop('hr.loan.management',v);first=r['loans'][0];self.assertEqual(first['total_due_minor'],'1001');self.assertEqual(first['paid_minor'],'600');self.assertEqual(first['outstanding_minor'],'401');self.assertFalse(first['settled']);self.assertEqual([x['amount_minor'] for x in first['installment_allocation']],['134','134','133']);self.assertEqual(sum(int(x['amount_minor']) for x in first['installment_allocation']),401);self.assertTrue(r['loans'][1]['settled']);self.assertIsNone(r['loans'][1]['installment_allocation']);self.assertEqual(r['total_outstanding_minor'],'401');self.assertFalse(r['interest_inferred']);self.assertFalse(r['ledger_posted']);self.assertFalse(r['payroll_deduction_scheduled'])

 def test_loan_rejects_overpayment_duplicates_and_allocation_without_balance(self):
  base={'id':'l1','employee':'e1','principal_minor':'100','charges_minor':'0','payments':[],'installments':0}
  with self.assertRaisesRegex(ERPError,'payment_exceeds_loan_total'):invoke('hr.loan.management',{'loans':[{**base,'payments':[{'id':'p1','amount_minor':'101'}]}]})
  with self.assertRaisesRegex(ERPError,'duplicate_loan'):invoke('hr.loan.management',{'loans':[base,base]})
  with self.assertRaisesRegex(ERPError,'duplicate_payment'):invoke('hr.loan.management',{'loans':[{**base,'payments':[{'id':'p1','amount_minor':'1'},{'id':'p1','amount_minor':'1'}]}]})
  with self.assertRaisesRegex(ERPError,'installments_require_outstanding_balance'):invoke('hr.loan.management',{'loans':[{**base,'payments':[{'id':'p1','amount_minor':'100'}],'installments':2}]})
  for bad in (True,'3',1.5):
   with self.assertRaisesRegex(ERPError,'invalid_quantity'):invoke('hr.loan.management',{'loans':[{**base,'installments':bad}]})
  with self.assertRaisesRegex(ERPError,'invalid_quantity'):invoke('hr.loan.management',{'loans':[{**base,'installments':1201}]})
  full={'principal_minor':'999999999999999999','charges_minor':'999999999999999999','payments':[],'installments':0}
  with self.assertRaisesRegex(ERPError,'amount_exceeds_storage_limit'):invoke('hr.loan.management',{'loans':[dict(full,id='l%d'%n,employee='e%d'%n) for n in range(500)]})

 def test_unknown_fields_and_bool_rows_never_become_success(self):
  for op,extra in [('hr.attendance',{'punches':[],'memo':'x'}),('hr.leave.management',{'balances':[],'requests':[],'x':1}),('hr.payroll',{'employees':[],'pay_date':'2026-10-26'}),('hr.time.overtime',{'days':[],'statutory':True}),('hr.expense.claim',{'policy':[],'claims':[],'approve':True}),('hr.loan.management',{'loans':[],'rate_bps':5000})]:
   with self.assertRaisesRegex(ERPError,'invalid_fields'):invoke(op,extra)
  with self.assertRaisesRegex(ERPError,'invalid_rows'):invoke('hr.attendance',{'punches':'all'})
  with self.assertRaisesRegex(ERPError,'invalid_fields'):invoke('hr.payroll',{'employees':[{'id':'e1','earnings':[],'deductions':[]},True]})



 def test_hr_reviewer_limits_installment_output_before_allocating(self):
  loan={'employee':'e','principal_minor':'1200','charges_minor':'0','payments':[],'installments':1200}
  with self.assertRaisesRegex(ERPError,'hr_installment_limit'):self.runop('hr.loan.management',{'loans':[{**loan,'id':'a'},{**loan,'id':'b'}]})
  with self.assertRaisesRegex(ERPError,'installment_below_one_minor'):self.runop('hr.loan.management',{'loans':[{**loan,'id':'a','principal_minor':'1','installments':2}]})
 def test_payment_reference_cannot_be_counted_on_two_loans(self):
  loan={'employee':'e','principal_minor':'10','charges_minor':'0','payments':[{'id':'same','amount_minor':'1'}],'installments':0}
  with self.assertRaisesRegex(ERPError,'duplicate_payment'):self.runop('hr.loan.management',{'loans':[{**loan,'id':'a'},{**loan,'id':'b'}]})
 def test_attendance_rejects_normalized_offsets_and_fraction_truncation(self):
  base={'id':'p','employee':'e','check_in':'2026-10-26T09:00:00Z','check_out':'2026-10-26T10:00:00Z'}
  for value in ['2026-10-26 09:00:00Z','2026-10-26T09:00:00+15:00','2026-10-26T09:00:00+14:30','2026-10-26T09:00:00+07:60','2026-10-26T09:00:00.0000001Z']:
   with self.assertRaisesRegex(ERPError,'invalid_timestamp'):self.runop('hr.attendance',{'punches':[{**base,'check_in':value}]})
 def test_leave_reports_recorded_and_simulated_balances_separately(self):
  r=self.runop('hr.leave.management',{'balances':[{'employee':'e','leave_type':'a','granted_units':10,'taken_units':2}],'requests':[{'id':'q','employee':'e','leave_type':'a','units':3}]});self.assertEqual(r['balances'][0]['recorded_remaining_units'],8);self.assertEqual(r['balances'][0]['remaining_units_after_requests'],5);self.assertTrue(r['request_simulation_only'])
 def test_large_hr_response_is_rejected_explicitly(self):
  loans=[{'id':str(n)+'x'*90,'employee':'😀'*100,'principal_minor':'1','charges_minor':'0','payments':[],'installments':0} for n in range(500)]
  with self.assertRaisesRegex(ERPError,'hr_result_limit'):self.runop('hr.loan.management',{'loans':loans})

 def test_payroll_component_budget_is_global(self):
  employee={'earnings':[{'component':'e'+str(n),'amount_minor':'0'} for n in range(100)],'deductions':[{'component':'d'+str(n),'amount_minor':'0'} for n in range(100)]}
  with self.assertRaisesRegex(ERPError,'hr_component_limit'):self.runop('hr.payroll',{'employees':[{**employee,'id':str(n)} for n in range(51)]})
 def test_loan_payment_processing_budget_is_global(self):
  loans=[{'id':str(n),'employee':str(n),'principal_minor':'100000','charges_minor':'0','payments':[{'id':str(n)+'-'+str(i),'amount_minor':'1'} for i in range(1000)],'installments':0} for n in range(11)]
  with self.assertRaisesRegex(ERPError,'hr_payment_limit'):self.runop('hr.loan.management',{'loans':loans})

 def test_invalid_unicode_identifiers_do_not_escape_validation(self):
  with self.assertRaisesRegex(ERPError,'invalid_id'):self.runop('hr.payroll',{'employees':[{'id':'\ud800','earnings':[],'deductions':[]}]})
