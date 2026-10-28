"""Actual licensed API contracts with an owned loopback fixture; restore config."""
import importlib.util,json,shlex,subprocess,sys,time,uuid
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'tests/debug_scanner'))
from test_registry import samples,SCOPE_OPS,PROBE_OPS,NEW_SAMPLES
from debug_scanner.registry import call
s=importlib.util.spec_from_file_location('qa_scanner_ssh',ROOT/'runtime/tools/ssh_connector.py');ssh=importlib.util.module_from_spec(s);s.loader.exec_module(ssh)
s=importlib.util.spec_from_file_location('qa_scanner_api',ROOT/'runtime/tools/master_cli.py');client=importlib.util.module_from_spec(s);s.loader.exec_module(client)
remote=r'''
import json,os,pathlib,threading,sys,uuid,stat
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
class Handler(BaseHTTPRequestHandler):
 def log_message(self,*args):pass
 def do_GET(self):
  self.send_response(200);self.send_header('Content-Type','text/html');self.send_header('Set-Cookie','session=SYNTHETIC_QA; HttpOnly; Secure; SameSite=Strict');self.end_headers();self.wfile.write(b'<h1>Harmonika owned fixture</h1>')
root=pathlib.Path('/opt/harmonika-ai');path=root/'debug-scanner-targets.json';assert not path.is_symlink() and stat.S_IMODE(path.stat().st_mode)==0o644,'target_registry_mode_requires_review';cfg=json.loads(path.read_text());assert set(cfg)=={'targets'} and type(cfg['targets']) is dict and len(cfg['targets'])<20
alias='scanner-qa-'+uuid.uuid4().hex[:12];server=ThreadingHTTPServer(('127.0.0.1',0),Handler);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start();entry={'url':'http://127.0.0.1:'+str(server.server_port),'paths':['/'],'public_probe':True,'allow_loopback_fixture':True}
def store(value):
 temporary=path.with_name('.debug-scanner-targets-'+uuid.uuid4().hex)
 try:
  fd=os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o644);os.fchmod(fd,0o644)
  with os.fdopen(fd,'w') as f:json.dump(value,f)
  os.replace(temporary,path)
 finally:
  if temporary.exists():temporary.unlink()
try:
 cfg['targets'][alias]=entry;store(cfg);print(json.dumps({'target_id':alias}),flush=True);sys.stdin.read()
finally:
 current=json.loads(path.read_text());assert current['targets'].get(alias)==entry,'qa_target_changed_externally';del current['targets'][alias];store(current);server.shutdown();server.server_close();thread.join(3);print('qa_fixture_and_target_removed',flush=True)
'''
results=[]
def invoke(op,data,*,raw=False):
 key=uuid.uuid4().hex
 for attempt in range(4):
  v=client.request('POST','/invoke',{'skill_id':op if raw else 'debug_scan.'+op,'input':data if raw or op=='catalog' else {'data':data}},idempotency_key=key)
  err=v.get('error')
  if not isinstance(err,dict) or err.get('code')!='analysis_busy' or attempt==3:break
  time.sleep(.25*(2**attempt))
 assert not err,'public_scanner_failure:'+op+':'+str(err.get('code'));return v.get('result',v)
with ssh.connection(ssh.target(ssh.load_config(),'vps')) as c:
 stdin,stdout,stderr=c.exec_command('python3 -u -c '+shlex.quote(remote),timeout=300);line=stdout.readline();target=json.loads(line)['target_id']
 try:
  for op,data in sorted(samples().items()):
   if op in SCOPE_OPS|PROBE_OPS:data={**data,'target_id':target}
   start=time.monotonic();r=invoke(op,data)
   if op=='catalog':assert r['catalog_size']==290 and r['callable_entrypoints']==len(samples()) and not r['all_290_operational']
   elif op in PROBE_OPS:assert r['result']['http_status']==200 and r['result']['provenance']=='server_registered_get_probe' and not r['result']['body_returned']
   elif op in SCOPE_OPS:assert r['result']['status']=='authorized'
   elif op=='scan':assert r['status']=='completed_with_limited_coverage' and not r['automatic_fix_applied'] and 'artifact' in r['report']
   else:assert not r['production_fix_applied'] and not r['business_data_modified']
   if op in NEW_SAMPLES:
    assert r==json.loads(json.dumps(call(op,data))),'public_result_mismatch:'+op
    invalid=client.request('POST','/invoke',{'skill_id':'debug_scan.'+op,'input':{'data':{'unexpected':True}}},idempotency_key=uuid.uuid4().hex)
    assert invalid.get('error'),'invalid_payload_accepted:'+op
   results.append({'skill_id':'debug_scan.'+op,'passed':True,'latency_ms':round((time.monotonic()-start)*1000,1)});print(json.dumps(results[-1]),flush=True);time.sleep(1.3)
  # Real existing software Chromium, supplied synthetic HTML, no external network.
  browser=invoke('workflow.browser',{'html':'<html><body><div style="width:900px">owned fixture</div><script>throw Error("synthetic QA")</script></body></html>','actions':[],'viewport':{'width':390,'height':844}},raw=True)
  assert browser['execution_kind']=='isolated_browser' and browser['data']['page_errors']>=1 and browser['data']['horizontal_overflow'] and len(browser['data']['screenshot_sha256'])==64
  denied=invoke('target.permission.check',{'target_id':'unregistered-qa'});assert denied['result']['status']=='denied'
  # Existing ERP endpoint remains available after additive registration.
  erp=invoke('erp_all.finance.invoice',{'data':{'lines':[{'unit_minor':'100','quantity':2}],'tax_bps':0}},raw=True);assert erp['result']['total_minor']=='200' and not erp['posted']
 finally:
  stdin.channel.shutdown_write();cleanup=stdout.read().decode();error=stderr.read().decode();status=stdout.channel.recv_exit_status();assert status==0 and 'qa_fixture_and_target_removed' in cleanup,'qa_fixture_cleanup_failed'
# Exercise actual CLI private artifact transport after target teardown.
p=subprocess.run([sys.executable,'-I',str(ROOT/'runtime/tools/scanner_cli.py'),'scan'],input=json.dumps({'files':[{'path':'main.py','content':'cursor.execute(f"SELECT * FROM items WHERE id={uid}")\n'}],'report_format':'pdf'}),capture_output=True,text=True,timeout=65);assert p.returncode==0,p.stdout[-200:];artifact=json.loads(p.stdout)['report']['artifact'];assert 'base64' not in p.stdout
report={'synthetic_only':True,'public_contracts_passed':len(results),'new_operation_invalid_payload_rejections':len(NEW_SAMPLES),'public_direct_result_parity_checks':len(NEW_SAMPLES),'tests':results,'registered_probe_fixture_removed':True,'live_browser':{'software_chromium':True,'page_errors_observed':browser['data']['page_errors'],'overflow_detected':True,'screenshot_sha256':browser['data']['screenshot_sha256']},'unregistered_target_denied':True,'erp_regression_passed':True,'pdf_artifact':artifact}
(ROOT/'docs/DEBUG-SCANNER-PUBLIC-RESULT.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps({'public_contracts_passed':len(results),'real_browser':True,'private_pdf_saved':True,'fixture_cleaned':True}))
