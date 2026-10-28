import base64,hashlib,io,json,sys,tempfile,threading,unittest
from pathlib import Path
from unittest.mock import patch
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'backend'))
from debug_scanner.common import ScannerError,bounded
from debug_scanner.targets import classify,scope_check,http_probe,approved
from debug_scanner.observations import inspect_logs,inspect_browser,inspect_performance
from debug_scanner.evidence import evidence_operation,analyze_causes
from debug_scanner.reports import report,pdf_document
SHA='a'*64
class Components(unittest.TestCase):
 def test_target_classification_never_probes_or_reads_files(self):
  for target,kind in [('https://example.invalid/p','web'),('/private/app.apk','mobile'),('program.exe','desktop'),('firmware.bin','firmware'),('192.168.0.0/24','network')]:
   r=classify('target.classify',{'target':target});self.assertEqual(r['kind'],kind);self.assertFalse(r['authorization_granted']);self.assertNotIn(target,json.dumps(r))
  with self.assertRaises(ValueError):classify('target.classify',{'target':'https://u:secret@example.invalid'})
 def test_permission_cannot_be_supplied_by_requester(self):
  with tempfile.TemporaryDirectory() as tmp:
   cfg=Path(tmp)/'targets.json';cfg.write_text('{"targets":{}}')
   r=scope_check('target.permission.check',{'target_id':'anything'},str(cfg));self.assertEqual(r['status'],'denied')
   with self.assertRaises(ValueError):approved({'target_id':'anything','authorized':True},str(cfg))
   with self.assertRaises(ValueError):approved({'url':'http://127.0.0.1'},str(cfg))
 def test_real_owned_http_probe_no_redirect_cookie_or_body_disclosure(self):
  class Handler(BaseHTTPRequestHandler):
   calls=[]
   def log_message(self,*args):pass
   def do_GET(self):
    self.__class__.calls.append(self.path);self.send_response(302 if self.path=='/redirect' else 200)
    self.send_header('Location','http://169.254.169.254/latest/meta-data/')
    self.send_header('Set-Cookie','session=PRIVATECOOKIE; HttpOnly; Secure; SameSite=Strict')
    self.send_header('Content-Type','text/html');self.end_headers();self.wfile.write(b'PRIVATE_RESPONSE_BODY')
  server=ThreadingHTTPServer(('127.0.0.1',0),Handler);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
  try:
   with tempfile.TemporaryDirectory() as tmp:
    cfg=Path(tmp)/'targets.json';cfg.write_text(json.dumps({'targets':{'owned':{'url':'http://127.0.0.1:'+str(server.server_port),'paths':['/','/redirect'],'public_probe':True,'allow_loopback_fixture':True}}}))
    allowed=scope_check('target.permission.check',{'target_id':'owned'},str(cfg));self.assertTrue(allowed['authorization_granted'])
    r=http_probe('dast.reproduce',{'target_id':'owned'},str(cfg));self.assertEqual(r['http_status'],200);self.assertTrue(r['cookie_flags'][0]['httponly']);self.assertNotIn('PRIVATE',json.dumps(r));self.assertFalse(r['bug_reproduced'])
    r=http_probe('dast.reproduce',{'target_id':'owned','path':'/redirect'},str(cfg));self.assertTrue(r['redirect_observed']);self.assertFalse(r['redirect_followed']);self.assertFalse(r['healthy']);self.assertEqual(Handler.calls,['/','/redirect'])
    with self.assertRaises(ValueError):http_probe('dast.reproduce',{'target_id':'owned','path':'/unauthorized'},str(cfg))
    with patch('socket.getaddrinfo',return_value=[(2,1,6,'',('8.8.8.8',80))]),patch('socket.create_connection') as connect:
     with self.assertRaises(ValueError):http_probe('dast.reproduce',{'target_id':'owned'},str(cfg))
     connect.assert_not_called()
  finally:server.shutdown();server.server_close();thread.join(3)
 def test_public_probe_rejects_private_dns_before_connect(self):
  with tempfile.TemporaryDirectory() as tmp:
   cfg=Path(tmp)/'targets.json';cfg.write_text(json.dumps({'targets':{'owned':{'url':'https://example.invalid','paths':['/'],'public_probe':True}}}))
   for address in ['127.0.0.1','169.254.169.254','0.0.0.0','::1','10.1.2.3','224.0.0.1']:
    with self.subTest(address=address),patch('socket.getaddrinfo',return_value=[(2,1,6,'',(address,443))]),patch('socket.create_connection') as connect:
     with self.assertRaises(ValueError):http_probe('dast.reproduce',{'target_id':'owned'},str(cfg))
     connect.assert_not_called()
 def test_probe_offline_is_degraded_not_false_success(self):
  with tempfile.TemporaryDirectory() as tmp:
   cfg=Path(tmp)/'targets.json';cfg.write_text(json.dumps({'targets':{'owned':{'url':'http://127.0.0.1:1','paths':['/'],'public_probe':True,'allow_loopback_fixture':True}}}))
   with patch('socket.create_connection',side_effect=TimeoutError):r=http_probe('dast.reproduce',{'target_id':'owned'},str(cfg))
   self.assertEqual(r['status'],'degraded');self.assertFalse(r['reachable'])
 def test_log_redaction_and_frames_without_secrets(self):
  value='Traceback\n File "/Users/private-person/project.py", line 12\nTypeError: password=SUPERSECRET email=person@example.invalid\nERROR: AddressSanitizer: heap-use-after-free'
  r=inspect_logs('evidence.stack.trace',{'logs':value});self.assertEqual(r['data']['frames'][0]['line'],12)
  for secret in ['SUPERSECRET','person@example.invalid','private-person']:self.assertNotIn(secret,json.dumps(r))
  self.assertFalse(r['root_cause_confirmed']);self.assertFalse(r['host_logs_read'])
  self.assertEqual(len(inspect_logs('asan.report.analyze',{'logs':value})['data']['events']),1)
 def test_browser_observations_are_not_claimed_live(self):
  r=inspect_browser('web.frontend.console.error',{'observations':[{'type':'console_error','line':4,'file':'/private/name.js'},{'type':'cors_error'}]});self.assertEqual(len(r['observations']),1);self.assertFalse(r['browser_launched']);self.assertNotIn('/private',json.dumps(r))
  r=inspect_browser('uiux.responsive.issue',{'observations':[{'type':'layout_overflow','viewport_width':390,'document_width':401}]});self.assertTrue(r['observations'][0]['overflow'])
 def test_performance_uses_explicit_samples_and_thresholds(self):
  r=inspect_performance('perf.benchmark.compare',{'samples_ms':[10,20,30,40,100],'threshold_ms':50,'baseline_ms':40});self.assertEqual(r['p95_ms'],100);self.assertEqual(r['p95_delta_ms'],60);self.assertFalse(r['profiler_attached'])
  r=inspect_performance('monitor.error.rate',{'total':200,'errors':5,'threshold_bps':100});self.assertEqual(r['error_rate_bps'],250);self.assertTrue(r['threshold_exceeded'])
 def test_rca_requires_real_references_never_invents_cause(self):
  v={'evidence':[{'id':'e1','method':'test','sha256':SHA}],'causes':[{'id':'c1','parent_id':None,'evidence_ids':['e1']}]}
  r=analyze_causes('rca.five.why',v);self.assertEqual(r['status'],'insufficient_evidence');self.assertFalse(r['root_cause_confirmed']);self.assertEqual(r['why_chains'],[['c1']])
  v['evidence'].append({'id':'e2','method':'static','sha256':'b'*64});v['causes'][0]['evidence_ids'].append('e2');r=analyze_causes('solution.verify.evidence',v);self.assertEqual(r['supported_cause_ids'],['c1']);self.assertFalse(r['independence_verified']);self.assertFalse(r['automatic_fix_permitted'])
  v['causes'][0]['evidence_ids']=['unknown']
  with self.assertRaises(ValueError):analyze_causes('rca.five.why',v)
 def test_timeline_orders_tz_real_dates_and_only_hashes(self):
  v={'events':[{'id':'later','timestamp':'2026-10-27T09:00:00+00:00','kind':'test','sha256':SHA},{'id':'earlier','timestamp':'2026-10-27T09:00:00+07:00','kind':'source','sha256':SHA}]};r=evidence_operation('evidence.timeline.build',v);self.assertEqual(r['events'][0]['id'],'earlier')
  v['events'][0]['timestamp']='2026-02-30T00:00:00Z'
  with self.assertRaises(ValueError):evidence_operation('evidence.timeline.build',v)
 def test_pdf_is_real_bounded_artifact_with_valid_xref(self):
  v={'findings':[{'id':'F1','rule_id':'python.sqli','severity':'high','line':7,'sha256':SHA}],'format':'pdf'};r=report('audit.report.generate',v);raw=base64.b64decode(r['artifact']['base64'],validate=True);self.assertTrue(raw.startswith(b'%PDF-1.4'));self.assertEqual(hashlib.sha256(raw).hexdigest(),r['artifact']['sha256']);self.assertIn(b'xref',raw);self.assertFalse(r['certified']);self.assertFalse(r['remediation_performed'])
  start=int(raw.rsplit(b'startxref\n',1)[1].splitlines()[0]);self.assertEqual(raw[start:start+4],b'xref')
 def test_ci_gate_does_not_deploy_or_certify(self):
  v={'findings':[{'id':'F1','rule_id':'candidate','severity':'critical','sha256':SHA}],'coverage_complete':True};r=report('cicd.block.if.critical',v);self.assertTrue(r['block']);self.assertFalse(r['pipeline_modified'])
  self.assertTrue(report('cicd.block.if.critical',{'findings':[]})['block'])
 def test_privileged_or_unknown_fields_and_complexity_rejected(self):
  for v in [{'x':float('nan')},{'x':'\ud800'},{'x':'a'*65536}]:
   with self.subTest(vtype=type(v['x'])),self.assertRaises(ValueError):bounded(v)
  with self.assertRaises(ValueError):inspect_logs('evidence.log.capture',{'logs':'foo','path':'/etc/passwd'})
  with self.assertRaises(ValueError):inspect_browser('web.frontend.console.error',{'observations':[{'type':{}}]})

class StaticIntegrationHardening(unittest.TestCase):
 def test_basename_collisions_keep_each_finding_and_private_paths_stay_hashed(self):
  from debug_scanner.static import static_scan
  files=[{'path':'first/app.py','content':'eval(user)\n'},{'path':'second/app.py','content':'eval(other)\n'}];r=static_scan('sast.code.review',{'files':files});labels={f['evidence']['file'] for f in r['findings']};self.assertEqual(len(labels),2)
  self.assertNotIn('first/',json.dumps(r));self.assertNotIn('second/',json.dumps(r))
 def test_constructed_identifier_still_unsafe_with_bound_values(self):
  from debug_scanner.static import static_scan
  source='def query(cursor, table, uid):\n    sql = f"SELECT * FROM {table} WHERE id=?"\n    cursor.execute(sql, (uid,))\n'
  r=static_scan('sec.sqli.test',{'files':[{'path':'query.py','content':source}]});self.assertEqual(len(r['findings']),1);self.assertFalse(r['confirmed_vulnerabilities'])
  safe='def query(cursor, uid):\n    sql = "SELECT * FROM items WHERE id=?"\n    cursor.execute(sql, (uid,))\n';self.assertEqual(static_scan('sec.sqli.test',{'files':[{'path':'query.py','content':safe}]})['findings'],[])
 def test_unsupported_security_language_is_not_reported_clean(self):
  from debug_scanner.static import static_scan
  for op in ('sec.sqli.test','sec.cmd.injection','lang.python.scan'):
   r=static_scan(op,{'files':[{'path':'main.java','content':'class Main {}'}]});self.assertEqual(r['status'],'needs_toolchain')
 def test_pipeline_produces_real_report_without_automatic_fix(self):
  from debug_scanner.registry import call
  r=call('scan',{'files':[{'path':'main.py','content':'cursor.execute(f"SELECT * FROM users WHERE id={uid}")\n'}],'report_format':'pdf'});self.assertIn('artifact',r['report']);self.assertFalse(r['automatic_fix_applied']);self.assertFalse(r['complete_security_assessment'])
