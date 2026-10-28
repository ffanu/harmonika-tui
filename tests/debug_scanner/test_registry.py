import asyncio,hashlib,importlib.util,json,sys,tempfile,threading,unittest
from pathlib import Path
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'backend'))
from debug_scanner.registry import ACTIVE_OPS,call,capabilities,STATIC_OPS,TARGET_OPS,SCOPE_OPS,PROBE_OPS,LOG_OPS,BROWSER_OPS,PERF_OPS,EVIDENCE_OPS,RCA_OPS,REPORT_OPS,ADVANCED_STATIC_OPS
from engine_registry.discovery import Discovery,SanitizedError
spec=importlib.util.spec_from_file_location('scanner_bundle',ROOT/'scripts/bundle-debug-scanner.py');bundle=importlib.util.module_from_spec(spec);spec.loader.exec_module(bundle)
SHA='a'*64
NEW_SAMPLES={
 'logic.state.machine':{'states':['OPEN','DONE'],'initial':'OPEN','terminal_states':['DONE'],'transitions':[{'from':'OPEN','to':'DONE','event':'finish'}],'trace':[{'from':'OPEN','to':'DONE','event':'finish'}]},
 'logic.workflow.bug':{'nodes':['a','b'],'dependencies':[{'node':'b','requires':'a'}],'trace':[{'node':'b','action':'start'}]},
 'logic.data.consistency':{'entities':[{'type':'user','id':'a'}],'references':[]},
 'logic.calculation.error':{'lines':[{'id':'a','quantity':3,'unit_minor':'15'}],'tax_bps':1000,'reported_subtotal_minor':'45','reported_tax_minor':'5','reported_total_minor':'50'},
 'logic.edge.case':{'cases':[{'id':'a','expected':True,'observed':1}]},
 'logic.integration.mismatch':{'fields':[{'name':'id','kind':'integer','required':True}],'records':[{'id':'a','payload':{'id':True}}],'allow_extra':False},
 'sec.jwt.vulnerability':{'token':'eyJhbGciOiJub25lIn0.eyJleHAiOjIwMDB9.','allowed_algorithms':['RS256'],'now_epoch':1000,'leeway_seconds':0},
 'sec.cors.misconfig':{'headers':{'Access-Control-Allow-Origin':'*','Access-Control-Allow-Credentials':'true'},'trusted_origins':['*'],'dynamic_origin':False},
 'uiux.color.contrast':{'foreground':'#FFFFFF','background':'#FFFFFF','large_text':False,'enhanced':False},
 'uiux.accessibility':{'html':'<html><img><button></button></html>'}}
for op in ('logic.race.condition','logic.concurrency'):NEW_SAMPLES[op]={'initial_versions':[{'resource':'stock','version':0}],'transactions':[{'id':'a','resource':'stock','started':0,'finished':5,'read_version':0,'write_version':1,'committed':True}]}

for op in ADVANCED_STATIC_OPS:NEW_SAMPLES[op]={'files':[{'path':'sample.py','content':'def f():\n    value = input()\n    return eval(value)\n    print(1)\n'}]}

def samples():
 rows={}
 for op in ACTIVE_OPS:
  if op in NEW_SAMPLES:rows[op]=NEW_SAMPLES[op]
  elif op in TARGET_OPS:rows[op]={'target':'https://example.invalid'}
  elif op in SCOPE_OPS|PROBE_OPS:rows[op]={'target_id':'fixture'}
  elif op in STATIC_OPS or op=='sec.owasp.top10':
   rows[op]={'files':[{'path':'main.py','content':'cursor.execute(f"SELECT * FROM items WHERE id={uid}")\n'},{'path':'main.js','content':'el.innerHTML = user;\n'},{'path':'package.json','content':'{"dependencies":{"example":"1.0.0"}}'}]}
  elif op in LOG_OPS:rows[op]={'logs':'File "/private/app.py", line 7\nTypeError: sensitive secret not returned\nAddressSanitizer: heap-use-after-free\nThreadSanitizer: data race\nruntime error: undefined behavior'}
  elif op in BROWSER_OPS:rows[op]={'observations':[{'type':'console_error','line':4,'file':'main.js'},{'type':'network_error'},{'type':'cors_error'},{'type':'csp_violation'},{'type':'layout_overflow','viewport_width':390,'document_width':420}]}
  elif op=='monitor.error.rate':rows[op]={'total':100,'errors':5,'threshold_bps':100}
  elif op in PERF_OPS:rows[op]={'samples_ms':[10,20,100],'threshold_ms':50,**({'baseline_ms':40} if op=='perf.benchmark.compare' else {})}
  elif op=='evidence.hash.compute':rows[op]={'content':'synthetic proof'}
  elif op=='evidence.timeline.build':rows[op]={'events':[{'id':'one','timestamp':'2026-10-27T09:00:00+07:00','kind':'test','sha256':SHA}]}
  elif op in RCA_OPS:rows[op]={'evidence':[{'id':'e1','method':'unit_test','sha256':SHA},{'id':'e2','method':'ast','sha256':SHA}],'causes':[{'id':'c1','parent_id':None,'evidence_ids':['e1','e2']}]}
  elif op in REPORT_OPS:rows[op]={'findings':[{'id':'F1','rule_id':'python.sqli','severity':'high','line':7,'sha256':SHA}],**({'format':'pdf'} if op!='cicd.block.if.critical' else {'coverage_complete':True})}
  else:raise AssertionError('fixture_missing:'+op)
 rows['catalog']={};rows['scan']={'files':[{'path':'main.py','content':'cursor.execute(f"SELECT * FROM items WHERE id={uid}")\n'}],'report_format':'pdf'};return rows

class Registry(unittest.TestCase):
 def test_exact_source_integrity_and_truthful_readiness(self):
  c=capabilities();self.assertEqual(c['catalog_size'],290);self.assertEqual(len(ACTIVE_OPS),69);self.assertEqual(c['callable_entrypoints'],71);self.assertFalse(c['all_290_operational']);self.assertEqual(c['source_sha256'],hashlib.sha256((ROOT/'backend/debug_scanner/specification.txt').read_bytes()).hexdigest());self.assertEqual([r['number'] for r in c['skills']],list(range(1,291)))
  self.assertFalse(c['features']['automatic_fix_or_deploy']);self.assertFalse(c['features']['compliance_certified'])
 def test_each_active_operation_rejects_unknown_input_and_unimplemented_never_succeeds(self):
  for op in ACTIVE_OPS|{'catalog','scan'}:
   with self.subTest(operation=op),self.assertRaises(ValueError):call(op,{'unexpected':True})
  for op in ('fix.production.deploy','mobile.android.scan','sec.auth.bypass','dast.fuzz.input','compliance.gdpr'):
   with self.subTest(operation=op),self.assertRaisesRegex(ValueError,'not_implemented'):call(op,{})
 def test_no_source_exec_or_fake_compliance(self):
  r=call('sec.owasp.top10',{'files':[{'path':'main.py','content':'cursor.execute(f"SELECT * FROM users WHERE id={uid}")\n'}]});self.assertFalse(r['result']['source_executed']);self.assertFalse(r['result']['confirmed_vulnerabilities']);self.assertFalse(r['result']['owasp_assessment_complete']);self.assertFalse(r['production_fix_applied'])
 def test_malformed_evidence_is_structured_error(self):
  bads=[('rca.five.why',{'evidence':[],'causes':[{'id':'one','parent_id':{},'evidence_ids':[]}]}),('evidence.timeline.build',{'events':[{'id':'e','timestamp':'x','kind':'test','sha256':SHA}]}),('audit.report.generate',{'findings':[{'id':'F1','rule_id':'x','severity':{},'sha256':SHA}]})]
  for op,v in bads:
   with self.subTest(op=op),self.assertRaises(ValueError):call(op,v)

 def test_pipeline_explicit_checks_and_privacy(self):
  v={'checks':[{'operation':'logic.edge.case','input':{'cases':[{'id':'a','expected':'PRIVATEVALUE','observed':'other'}]}},{'operation':'uiux.color.contrast','input':NEW_SAMPLES['uiux.color.contrast']}],'report_format':'pdf'}
  r=call('scan',v);self.assertEqual(len(r['parts']['checks']),2);self.assertNotIn('PRIVATEVALUE',json.dumps(r));self.assertFalse(r['complete_security_assessment']);self.assertIn('artifact',r['report'])
  with self.assertRaises(ValueError):call('scan',{'checks':[{'operation':'exec.shell','input':{}}]})
  cycle=call('logic.workflow.bug',{'nodes':['a'],'dependencies':[{'node':'a','requires':'a'}],'trace':[]})
  from debug_scanner.registry import pipeline_logic_issues
  self.assertEqual(pipeline_logic_issues(cycle['result']['data'])[0]['rule_id'],'WORKFLOW.CYCLE_DETECTED')

class DiscoveryContracts(unittest.IsolatedAsyncioTestCase):
 async def test_all_active_hash_verified_plugins_execute_and_reject_bad_envelope(self):
  class Handler(BaseHTTPRequestHandler):
   def log_message(self,*args):pass
   def do_GET(self):self.send_response(200);self.send_header('Content-Type','text/plain');self.end_headers();self.wfile.write(b'owned fixture')
  server=ThreadingHTTPServer(('127.0.0.1',0),Handler);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
  try:
   with tempfile.TemporaryDirectory() as tmp:
    root=Path(tmp).resolve();root.chmod(0o755);cfg=root/'targets.json';cfg.write_text(json.dumps({'targets':{'fixture':{'url':'http://127.0.0.1:'+str(server.server_port),'paths':['/'],'public_probe':True,'allow_loopback_fixture':True}}}));cfg.chmod(0o644)
    report=bundle.bundle(root/'plugins',target_config_path=str(cfg));self.assertEqual(report['plugins'],71)
    d=Discovery(root/'plugins',root/'isolated.sqlite',reserved_ids=[]);await d.refresh();self.assertEqual(d.summary()['registered'],71,d.summary())
    for op,v in sorted(samples().items()):
     with self.subTest(operation=op):
      result=await d.execute('debug_scan.'+op,{} if op=='catalog' else {'data':v});self.assertIsInstance(result,dict)
      if op in PROBE_OPS:self.assertEqual(result['result']['http_status'],200);self.assertFalse(result['result']['body_returned'])
      elif op in SCOPE_OPS:self.assertEqual(result['result']['status'],'authorized')
      elif op=='scan':self.assertFalse(result['automatic_fix_applied']);self.assertIn('artifact',result['report'])
      elif op=='catalog':self.assertEqual(result['catalog_size'],290)
      if op not in PROBE_OPS|SCOPE_OPS:self.assertEqual(result,json.loads(json.dumps(call(op,v))))
      if op not in {'catalog','scan'}:self.assertFalse(result['production_fix_applied'])
      with self.assertRaises(SanitizedError):await d.execute('debug_scan.'+op,{'unknown':True})
    d.close()
  finally:server.shutdown();server.server_close();thread.join(3)

class PackagingIntegrity(unittest.TestCase):
 def test_trusted_embedded_source_is_hash_checked_before_execution(self):
  with tempfile.TemporaryDirectory() as tmp:
   bundle.bundle(Path(tmp));source=(Path(tmp)/'debug_scan.catalog/skill.py').read_text();self.assertIn('LZMADecompressor',source);self.assertNotIn('import ast\n',source)
   import re,asyncio
   broken=re.sub(r"_RAW_SHA='[0-9a-f]{64}'","_RAW_SHA='"+'0'*64+"'",source);ns={};exec(compile(broken,'<owned-test>','exec'),ns)
   with self.assertRaisesRegex(ValueError,'scanner_source_integrity_failed'):asyncio.run(ns['Execute']({}))
