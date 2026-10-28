#!/usr/bin/env python3
"""Synthetic ERP public rejection paths; no business/customer access."""
import importlib.util,json,time,uuid
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
s=importlib.util.spec_from_file_location('reject_client',ROOT/'runtime/tools/master_cli.py');client=importlib.util.module_from_spec(s);s.loader.exec_module(client)
lines=[]
for n in range(10):lines.extend([{'account':'d'+str(n),'debit_minor':'999999999999999999','credit_minor':'0'},{'account':'c'+str(n),'debit_minor':'0','credit_minor':'999999999999999999'}])
cases=[('analysis_missing_data','finance.invoice',{'lines':[{'unit_minor':'100','quantity':1}],'tax_bps':0},'invalid_skill_input'),('malformed_modules','plan',{'spec':'ISP','industry':'isp','optional':False},'invalid_skill_input'),('module_conflict','module.conflict.detect',{'spec':'ISP','industry':'isp','optional':['hr'],'exclude':['hr']},'skill_execution_failed'),('journal_aggregate_overflow','finance.journal.entry',{'data':{'lines':lines}},'skill_execution_failed'),('remote_brand_logo','theme.branding.apply',{'spec':'ISP','industry':'isp','branding':{'logo_path':'https://example.invalid/logo.png'}},'skill_execution_failed')]
rows=[]
for name,op,payload,expected in cases:
 v=client.request('POST','/invoke',{'skill_id':'erp_all.'+op,'input':payload},idempotency_key=uuid.uuid4().hex)
 assert isinstance(v.get('error'),dict) and v['error'].get('code')==expected,name+':unexpected_error'
 assert 'Traceback' not in json.dumps(v) and 'password' not in json.dumps(v).lower(),name+':unsafe_error'
 rows.append({'scenario':name,'passed':True,'error_code':expected});time.sleep(1.3)
(ROOT/'docs/ERP-PUBLIC-REJECTIONS-RESULT.json').write_text(json.dumps({'synthetic_only':True,'tests':rows},indent=2)+'\n');print(json.dumps({'public_rejections_passed':len(rows)}))
