#!/usr/bin/env python3
"""Synthetic sourcing qualification through installed licensed CLI; no business data."""
import hashlib,importlib.util,json,subprocess,sys,zipfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'backend'))
s=importlib.util.spec_from_file_location('sourcing_tests',ROOT/'tests/erp/test_sourcing.py');m=importlib.util.module_from_spec(s);s.loader.exec_module(m);fixture=m.SourcingAnalysis()
def cli(args,data):
 p=subprocess.run(['harmonika','erp',*args],input=json.dumps(data),text=True,capture_output=True,timeout=90)
 assert p.returncode==0,p.stdout[-600:];v=json.loads(p.stdout);assert not v.get('error'),v;return v
results=[]
for op,data in [('procurement.rfq',fixture.rfq()),('procurement.vendor.performance',fixture.performance())]:
 v=cli(['analyze',op],data);r=v['result'];assert v['scope']=='caller_supplied_data_only' and not v['posted'];assert not r['business_data_updated'] and not r['source_evidence_verified']
 if op=='procurement.rfq':assert r['lowest_total_minor']=='400' and r['lowest_total_vendors']==['beta'] and not r['supplier_awarded']
 else:assert r['vendors'][0]['vendor']=='beta' and r['vendors'][0]['score_bps']==10000
 results.append({'operation':op,'passed':True})
data={'spec':'ERP jasa dan proyek','company':'Harmonika QA','industry':'service','optional':['projects']}
v=cli(['generate'],data);path=Path(v['artifact_path']);raw=path.read_bytes();assert hashlib.sha256(raw).hexdigest()==v['sha256']
from erp_engine.generator import generate
expected=generate(**data)
with zipfile.ZipFile(path) as z:
 assert set(z.namelist())==set(expected['files'])
 for name,value in expected['files'].items():assert z.read(name)==value.encode(),name
 manifest=json.loads(z.read('source-manifest.json'))
 for name,pin in manifest.items():assert hashlib.sha256(z.read(name)).hexdigest()==pin
assert not v['built'] and not v['deployed'] and not v['tests_run_for_this_artifact']
report={'synthetic_input_only':True,'tests':results,'latest_download':{'path':str(path),'sha256':v['sha256'],'files':len(expected['files']),'built':False,'deployed':False,'tests_run_for_this_download':False,'all_template_bytes_verified':True,'manifest_verified':True}}
(ROOT/'docs/ERP-PUBLIC-SOURCING-RESULT.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps({'public_sourcing_passed':len(results),'source_files_verified':len(expected['files'])}))
