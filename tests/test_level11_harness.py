import ast,base64,hashlib,hmac,json,tempfile,unittest,re,os,time,types,threading,urllib.request
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
from email.message import Message
SRC=Path(__file__).resolve().parents[1]/'scripts/level11-lab'
def functions(file,names,namespace):
 nodes=[n for n in ast.parse((SRC/file).read_text()).body if isinstance(n,ast.FunctionDef) and n.name in names];exec(compile(ast.Module(body=nodes,type_ignores=[]),file,'exec'),namespace);return namespace
class Guards(unittest.TestCase):
 def test_holder_state_before_start_is_explicit_without_spending_fault_budget(self):
  state={'closed':False,'release':'atlas-v1'};rows=[];ns=functions('controller.py',['operation'],{'S':state,'record':rows.append,'time':time});value=ns['operation']({'op':'lock','action':'state','origin':'agent'});self.assertEqual(value,{'state':'not_started','started':False});self.assertEqual(state['holders']['atlas-v1'],{});self.assertEqual(rows[0]['action'],'state')
 def test_log_fixture_exception_never_exempts_application_or_changed_logs(self):
  tree=ast.parse((SRC.parent/'level11-grade.py').read_text());fn=next(n for n in ast.walk(tree) if isinstance(n,ast.FunctionDef) and n.name=='application_logs_and_historical_fixture')
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);source=root/'failed-fixture.txt';source.write_bytes(b'synthetic traceback');digest=hashlib.sha256(source.read_bytes()).hexdigest();(root/'grader-log-incident.json').write_text(json.dumps({'container_id':'helper','output_sha256':digest,'source':source.name}));ledger={'containers':['helper'],'activations':{}};row={'container_id':'helper','output_sha256':digest,'private_canary_present':True};ns={'root':root,'ledger':ledger,'logs':{'scans':[row]},'json':json,'hashlib':hashlib};exec(compile(ast.Module(body=[fn],type_ignores=[]),'grader','exec'),ns);self.assertTrue(ns['application_logs_and_historical_fixture']())
   ledger['activations']={'release':{'container_id':'helper'}}
   with self.assertRaises(AssertionError):ns['application_logs_and_historical_fixture']()
   ledger['activations']={};row['output_sha256']='changed'
   with self.assertRaises(AssertionError):ns['application_logs_and_historical_fixture']()
   row['output_sha256']=digest;row['container_id']='unknown'
   with self.assertRaises(AssertionError):ns['application_logs_and_historical_fixture']()
 def test_browser_proxy_forwards_real_socket_requests(self):
  requests=[]
  def call(value):
   requests.append(value);return {'http_status':200,'body_b64':base64.b64encode(b'fixture-response').decode(),'headers':{'Content-Type':'text/plain'}}
  nodes=[n for n in ast.parse((SRC/'browser_proxy.py').read_text()).body if isinstance(n,ast.ClassDef)];ns={'BaseHTTPRequestHandler':BaseHTTPRequestHandler,'base64':base64,'call':call};exec(compile(ast.Module(body=nodes,type_ignores=[]),'browser_proxy.py','exec'),ns)
  server=ThreadingHTTPServer(('127.0.0.1',0),ns['Handler']);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
  try:
   for method in ['GET','POST','PATCH']:
    request=urllib.request.Request('http://127.0.0.1:'+str(server.server_port)+'/p/one/tickets',data=b'{}' if method!='GET' else None,method=method)
    with urllib.request.urlopen(request,timeout=2) as response:self.assertEqual(response.read(),b'fixture-response')
   self.assertEqual([r['method'] for r in requests],['GET','POST','PATCH']);self.assertEqual(base64.b64decode(requests[1]['raw_b64']),b'{}')
  finally:server.shutdown();thread.join();server.server_close()
 def auth(self):
  temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup);root=Path(temp.name);(root/'examiner-auth.key').write_bytes(b'synthetic-key');return functions('controller.py',['authorize'],{'ROOT':root,'hmac':hmac,'hashlib':hashlib,'json':json})
 def test_signed_examiner_and_tamper(self):
  ns=self.auth();r={'op':'cleanup','origin':'examiner'};sig=hmac.new(b'synthetic-key',json.dumps(r,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode(),hashlib.sha256).hexdigest();ns['authorize']({**r,'_operator_auth':sig})
  for value in ['',sig+'a','é']:
   with self.assertRaises(ValueError):ns['authorize']({**r,'_operator_auth':value})
 def test_agent_cannot_cleanup_or_read_examiner(self):
  ns=self.auth()
  for op in ['cleanup','test-image','test-boreal']:
   with self.assertRaises(ValueError):ns['authorize']({'op':op,'origin':'agent'})
 def validation(self):return functions('controller.py',['validate_http'],{'re':re,'json':json})['validate_http']
 def test_no_arbitrary_http_targets_or_scope(self):
  valid={'owner':'alpha','project':'one','method':'GET','path':'/jobs'};self.validation()(valid)
  for bad in [{'owner':'root'},{'project':'../../other'},{'path':'https://outside/'},{'path':'//host/'},{'path':'/../secret'},{'method':'DELETE'}]:
   with self.assertRaises((ValueError,AssertionError)):self.validation()({**valid,**bad})
 def test_crash_only_owned_job_post(self):
  v={'owner':'alpha','project':'one','method':'POST','path':'/jobs','lost_ack':True};self.validation()(v)
  for bad in [{'method':'GET'},{'path':'/tickets'},{'channel':'browser'}]:
   with self.assertRaises(ValueError):self.validation()({**v,**bad})
 def identity(self):return functions('server.py',['identity'],{'CONFIG':{'alpha':'a-fixture','beta':'b-fixture'},'hmac':hmac})['identity']
 def headers(self,*rows):
  h=Message()
  for k,v in rows:h[k]=v
  return h
 def test_auth_rejects_nonascii_missing_or_duplicate(self):
  f=self.identity();self.assertEqual(f(self.headers(('Authorization','Bearer a-fixture'))),'alpha')
  for h in [self.headers(),self.headers(('Authorization','Bearer café')),self.headers(('Authorization','Bearer wrong')),self.headers(('Authorization','Bearer a-fixture'),('Authorization','Bearer b-fixture'))]:self.assertIsNone(f(h))
 def test_fixed_network_resources_and_no_ports(self):
  s=(SRC/'controller.py').read_text();self.assertNotIn('shell=True',s);self.assertNotIn('--privileged',s);self.assertNotIn('--publish',s);self.assertIn("'--network','none'",s);self.assertIn("'--user','10001:10001'",s);self.assertIn("'--cap-drop','ALL'",s);self.assertIn("'--memory','256m'",s)
 def test_no_raw_payload_or_authorization_in_mechanical_receipts(self):
  tree=ast.parse((SRC/'controller.py').read_text());http=next(x for x in tree.body if isinstance(x,ast.FunctionDef) and x.name=='http');statement=next(x for x in ast.walk(http) if isinstance(x,ast.Call) and isinstance(x.func,ast.Attribute) and x.func.attr=='append');keys={k.value for n in ast.walk(statement) if isinstance(n,ast.Dict) for k in n.keys if isinstance(k,ast.Constant)};self.assertIn('key_sha256',keys);self.assertIn('body_sha256',keys);self.assertFalse({'headers','body','key'}&keys)
 def test_holder_natural_release_no_signals(self):
  s=(SRC/'holder.py').read_text();self.assertIn('time.monotonic()+45',s);self.assertNotIn('os.kill',s);self.assertNotIn('signal',s.replace('no process signals',''))
 def test_envelope_fields_override_inner_holder_kind(self):
  s=(SRC/'controller.py').read_text();self.assertIn("record({**value,'kind':'lock'",s)
 def test_agent_ledger_filters_examiner_metadata(self):
  state={'closed':False,'release':'atlas-v1','active':'owned','releases':{'atlas-v1':{'image_id':'image','source_sha256':'sha','files':{'hidden':'contents'}}},'activations':{},'requests':[{'origin':'agent','id':1},{'origin':'examiner','id':2}],'containers':['private-grader']};ns=functions('controller.py',['operation'],{'S':state});v=ns['operation']({'op':'ledger','origin':'agent'});self.assertEqual(v['requests'],[{'origin':'agent','id':1}]);self.assertNotIn('containers',v);self.assertNotIn('files',v['releases']['atlas-v1']);state['requests']=[{'origin':'agent','id':n} for n in range(12)];v=ns['operation']({'op':'ledger','origin':'agent'});self.assertEqual(len(v['requests']),8);self.assertEqual(v['requests_total'],12);self.assertTrue(v['requests_truncated']);self.assertEqual(ns['operation']({'op':'ledger','origin':'examiner'})['requests'],state['requests'])
 def test_archive_uses_actual_main_events_without_actor_payload(self):
  rows=[{'Type':'container','Action':'die','timeNano':99000000000,'Actor':{'ID':'owned','Attributes':{'exitCode':'42','payload':'PRIVATE_DATA_CANARY'}}},{'Type':'container','Action':'start','timeNano':99500000000,'Actor':{'ID':'foreign'}}];state={'created_at':98,'containers':['owned']};ns=functions('controller.py',['capture_events'],{'S':state,'META':{'lab_id':'lab'},'LABEL':'label','run':lambda *a,**k:types.SimpleNamespace(returncode=0,stdout='\n'.join(map(json.dumps,rows))),'time':types.SimpleNamespace(time=lambda:100.4),'json':json,'save':lambda:None});ns['capture_events']();self.assertEqual(state['event_archive'],{'owned':[{'action':'die','at':99.0,'exit_code':'42'}]});self.assertNotIn('PRIVATE_DATA_CANARY',json.dumps(state));ns['capture_events']();self.assertEqual(len(state['event_archive']['owned']),1)
 def test_ledger_publication_is_private_and_readable_after_fsync(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);ns=functions('controller.py',['save'],{'ROOT':root,'S':{'owned':True},'json':json,'os':os});ns['save']();self.assertEqual(json.loads((root/'ledger.json').read_text()),{'owned':True});self.assertEqual((root/'ledger.json').stat().st_mode&0o777,0o600)
if __name__=='__main__':unittest.main(verbosity=2)
