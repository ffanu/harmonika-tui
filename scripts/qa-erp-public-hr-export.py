#!/usr/bin/env python3
"""Synthetic public HR/export smoke; no actual employees, payments or private DB."""
import hashlib,io,json,stat,subprocess,zipfile
from pathlib import Path
from xml.etree import ElementTree as ET
ROOT=Path(__file__).resolve().parents[1]
CASES={
 'hr.attendance':{'punches':[{'id':'p','employee':'e','check_in':'2026-10-26T09:00:00+07:00','check_out':'2026-10-26T10:00:00+07:00'}]},
 'hr.leave.management':{'balances':[{'employee':'e','leave_type':'a','granted_units':10,'taken_units':2}],'requests':[{'id':'r','employee':'e','leave_type':'a','units':3}]},
 'hr.payroll':{'employees':[{'id':'e','earnings':[{'component':'base','amount_minor':'1000'}],'deductions':[{'component':'provided','amount_minor':'100'}]}]},
 'hr.time.overtime':{'days':[{'id':'d','employee':'e','date':'2026-10-26','regular_minutes':480,'worked_minutes':510,'overtime_rate_minor_per_hour':'1001'}]},
 'hr.expense.claim':{'policy':[{'category':'travel','cap_minor':'100'}],'claims':[{'id':'c','employee':'e','category':'travel','amount_minor':'120'}]},
 'hr.loan.management':{'loans':[{'id':'l','employee':'e','principal_minor':'1001','charges_minor':'0','payments':[{'id':'p','amount_minor':'600'}],'installments':3}]},
}
def invoke(op,data):
 p=subprocess.run(['harmonika','erp','analyze',op],input=json.dumps(data),capture_output=True,text=True,timeout=45)
 value=json.loads(p.stdout);assert not value.get('error'),'public_skill_failed:'+op;return value
results=[]
for op,data in CASES.items():
 response=invoke(op,data);r=response['result'];assert response['scope']=='caller_supplied_data_only' and not response['posted'];assert not r['business_data_updated'] and not r['disbursement_sent'] and not r['compliance_verified']
 if op=='hr.attendance':assert r['total_seconds']==3600
 elif op=='hr.leave.management':assert r['balances'][0]['recorded_remaining_units']==8 and r['balances'][0]['remaining_units_after_requests']==5
 elif op=='hr.payroll':assert r['net_minor']=='900' and not r['payroll_disbursed']
 elif op=='hr.time.overtime':assert r['total_amount_minor']=='501'
 elif op=='hr.expense.claim':assert r['over_policy_minor']=='20' and not r['receipts_verified']
 else:assert r['total_outstanding_minor']=='401' and [x['amount_minor'] for x in r['loans'][0]['installment_allocation']]==['134','134','133']
 results.append({'operation':op,'passed':True})
value=invoke('report.export.excel',{'title':'Harmonika QA','headers':['ID','Note'],'rows':[['000123','=1+1']]});assert value['status']=='report_exported' and 'base64' not in value
path=Path(value['artifact_path']);data=path.read_bytes();assert hashlib.sha256(data).hexdigest()==value['sha256'];assert stat.S_IMODE(path.stat().st_mode)==0o600
with zipfile.ZipFile(io.BytesIO(data)) as z:
 ns={'m':'http://schemas.openxmlformats.org/spreadsheetml/2006/main'};sheet=ET.fromstring(z.read('xl/worksheets/sheet1.xml'));cells=sheet.findall('.//m:c',ns);assert cells[2].find('m:is/m:t',ns).text=='000123';assert cells[3].find('m:is/m:t',ns).text=='=1+1';assert cells[3].get('t')=='inlineStr' and not sheet.findall('.//m:f',ns)
results.append({'operation':'report.export.excel','passed':True,'actual_file_saved':True,'sha_verified':True,'formula_stored_as_text':True})
(ROOT/'docs/ERP-PUBLIC-HR-EXPORT-RESULT.json').write_text(json.dumps({'synthetic_input_only':True,'tests':results,'artifact_path':str(path)},indent=2)+'\n')
print(json.dumps({'public_hr_analyses_passed':6,'verified_xlsx_saved':True}))
