#!/usr/bin/env python3
"""Exercise all reviewed active ERP contracts via licensed public API, synthetic only."""
import importlib.util,json,sys,time,uuid
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'tests/erp'))
from test_contract_matrix import cases,check_output,invoke,ANALYSIS_OPS
s=importlib.util.spec_from_file_location('qualified_client',ROOT/'runtime/tools/master_cli.py');client=importlib.util.module_from_spec(s);s.loader.exec_module(client)
rows=[]
for op,payload in sorted(cases().items()):
 key=uuid.uuid4().hex;start=time.monotonic()
 deadline=time.monotonic()+70;attempt=0
 while True:
  envelope=client.request('POST','/invoke',{'skill_id':'erp_all.'+op,'input':payload},idempotency_key=key)
  error=envelope.get('error')
  code=error.get('code') if isinstance(error,dict) else None
  if code not in ('analysis_busy','rate_limit') or time.monotonic()>=deadline or code=='analysis_busy' and attempt>=3:break
  time.sleep(min(5 if code=='rate_limit' else .25*(2**attempt),max(0,deadline-time.monotonic())));attempt+=1
 assert not error,'public_contract_failed:'+op+':'+str(error.get('code'))
 result=check_output(op,envelope.get('result',envelope))
 expected=invoke(op,payload['data'] if op in ANALYSIS_OPS else payload)
 if 'files' in expected:expected['source_file_count']=len(expected.pop('files'))
 assert result==expected,'public_result_differs_from_reviewed_contract:'+op
 rows.append({'skill_id':'erp_all.'+op,'passed':True,'latency_ms':round((time.monotonic()-start)*1000,1)})
 print(json.dumps(rows[-1]),flush=True)
 time.sleep(1.3)
report={'synthetic_only':True,'total':len(rows),'passed':len(rows),'failed':0,'scope':'pure_entrypoint_contracts_not_business_deployment','tests':rows}
(ROOT/'docs/ERP-ACTIVE-CONTRACTS-RESULT.json').write_text(json.dumps(report,indent=2)+'\n')
