import ast,hashlib,hmac,http.client,importlib.util,json,socket,sqlite3,sys,tempfile,threading,time,types,unittest
from pathlib import Path
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from unittest.mock import patch
SRC=Path(__file__).resolve().parents[1]/'scripts/level9-lab'
class ProxyTransport(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.commits=0;self.raw=b'{"kind":"report","text":"PRIVATE_DATA_CANARY"}';test=self
  class Upstream(BaseHTTPRequestHandler):
   protocol_version='HTTP/1.1'
   def log_message(self,*a):pass
   def do_POST(self):self.rfile.read(int(self.headers.get('Content-Length','0')));test.commits+=1;self.reply(json.dumps({'job':{'id':'a'*32,'owner':'alpha','state':'queued'}}).encode())
   def do_GET(self):self.reply(test.raw if self.path.startswith('/artifacts/') else b'{"ready":true}')
   def reply(self,raw):self.send_response(200);self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
  self.up=ThreadingHTTPServer(('127.0.0.1',0),Upstream);self.up.daemon_threads=True;self.ut=threading.Thread(target=self.up.serve_forever);self.ut.start()
  nodes=[n for n in ast.parse((SRC/'proxy.py').read_text()).body if isinstance(n,(ast.Import,ast.ImportFrom,ast.FunctionDef,ast.ClassDef,ast.With))];self.ns={'ROOT':self.root,'RELEASE':'workflow-v3'};exec(compile(ast.Module(body=nodes,type_ignores=[]),'protected_proxy','exec'),self.ns)
  original=http.client.HTTPConnection
  def connection(host,port=None,*a,**kw):return original('127.0.0.1',self.up.server_port,*a,**kw) if host=='api' else original(host,port,*a,**kw)
  self.mapping=patch('http.client.HTTPConnection',connection);self.mapping.start();self.proxy=self.ns['Server'](('127.0.0.1',0),self.ns['Handler']);self.pt=threading.Thread(target=self.proxy.serve_forever);self.pt.start()
 def tearDown(self):
  (self.root/'release-held').touch();self.proxy.shutdown();self.proxy.server_close();self.pt.join(2);self.mapping.stop();self.up.shutdown();self.up.server_close();self.ut.join(2);self.tmp.cleanup()
 def request(self,path='/jobs',method='POST',identity='coordinator1',read=True):
  c=http.client.HTTPConnection('127.0.0.1',self.proxy.server_port,timeout=3)
  try:
   c.request(method,path,body=self.raw if method=='POST' else None,headers={'X-Lab-Coordinator':identity,'X-Lab-Owner':'alpha','Idempotency-Key':'stable'});r=c.getresponse();return r.status,r.read() if read else b''
  finally:c.close()
 def arm(self,name,target='all'):
  with sqlite3.connect(self.root/'network.sqlite') as c:c.execute('INSERT INTO faults VALUES(?,?,?,1)',('workflow-v3',name,target))
 def events(self):
  with sqlite3.connect(self.root/'network.sqlite') as c:c.row_factory=sqlite3.Row;return [dict(x) for x in c.execute('SELECT * FROM calls')]
 def test_actual_tcp_forward_and_private_mechanical_audit(self):
  self.assertEqual(self.request()[0],200);self.assertEqual(self.commits,1);text=json.dumps(self.events());self.assertNotIn('PRIVATE_DATA_CANARY',text);self.assertNotIn('stable',text);self.assertIn('forward',text)
 def test_actual_eof_after_commit_and_one_shot_budget(self):
  self.arm('drop-ack')
  with self.assertRaises(http.client.HTTPException):self.request()
  self.assertEqual(self.commits,1);self.assertEqual(sum(x['event']=='ack_dropped' for x in self.events()),1);self.assertFalse(self.ns['take']('drop-ack','coordinator1'))
 def test_actual_truncated_body_is_incomplete_not_success(self):
  self.arm('partial-artifact')
  with self.assertRaises(http.client.IncompleteRead):self.request('/artifacts/'+'a'*32,'GET')
  self.assertEqual(self.request('/artifacts/'+'a'*32,'GET'),(200,self.raw));self.assertEqual(self.commits,0)
 def test_partition_closes_before_forward_then_recovers(self):
  (self.root/'network-partition').touch()
  with self.assertRaises(http.client.HTTPException):self.request()
  self.assertEqual(self.commits,0);(self.root/'network-partition').unlink();self.assertEqual(self.request()[0],200);self.assertEqual(self.commits,1)
 def test_fixed_scope_rejects_arbitrary_url_and_identity(self):
  self.assertEqual(self.request('http://example.invalid/private','GET')[0],403);self.assertEqual(self.request(identity='unknown')[0],403);self.assertEqual(self.commits,0)
 def test_held_response_does_not_block_other_coordinator(self):
  self.arm('hold-after-submit','coordinator1');result=[];t=threading.Thread(target=lambda:result.append(self.request()));t.start()
  try:
   for _ in range(100):
    if any(x['event']=='response_hold' for x in self.events()):break
    time.sleep(.01)
   else:self.fail('hold_not_observed')
   self.assertTrue(t.is_alive());self.assertEqual(self.commits,1);self.assertEqual(self.request('/queue-readyz','GET',identity='coordinator2')[0],200)
  finally:(self.root/'release-held').touch();t.join(3)
  self.assertFalse(t.is_alive());self.assertEqual(result[0][0],200);self.assertTrue(any(x['event']=='response_released' for x in self.events()))
 def test_one_shot_target_and_release_isolation(self):
  self.arm('drop-ack','coordinator1');self.assertFalse(self.ns['take']('drop-ack','coordinator2'));self.assertTrue(self.ns['take']('drop-ack','coordinator1'));self.assertFalse(self.ns['take']('drop-ack','coordinator1'));self.ns['RELEASE']='workflow-v3-r1';self.assertFalse(self.ns['take']('drop-ack','coordinator1'))
class TemporalGrading(unittest.TestCase):
 def fixture(self):
  import copy
  source=ast.parse((SRC.parent/'level9-grade.py').read_text());node=next(x for x in ast.walk(source) if isinstance(x,ast.FunctionDef) and x.name=='stale');wid='1'*32;child='a'*32;h=hashlib.sha256(wid.encode()).hexdigest();cid='owned-coordinator1';ch=hashlib.sha256(cid.encode()).hexdigest();release='workflow-v3'
  hold={'id':10,'event':'response_hold','coordinator_id':'coordinator1','job_id':child,'at':100};released={'id':11,'event':'response_released','job_id':child,'at':110}
  ns={'hashlib':hashlib,'case':lambda _: {'id':wid},'root_step':lambda _: {'child_id':child},'tcp_fault':lambda *_:hold,'current':[hold,released],'release':release,'audit':[{'id':1,'workflow_id':wid,'task':'prepare','event':'claim','coordinator_id':'coordinator1','epoch':1},{'id':2,'workflow_id':wid,'task':'prepare','event':'settle','coordinator_id':'coordinator2','epoch':2},{'id':3,'workflow_id':wid,'task':'prepare','event':'stale_rejected','coordinator_id':'coordinator1','epoch':1}],'status':{'roles':{'coordinator1':{'container_id':cid}}},'events':{'containers':[{'container_sha256':ch,'events':[]}]},'obs':[{'kind':'database_witness','origin':'agent','release':release,'at':105,'claim_trace':[{'id':2,'workflow_id_sha256':h}]},{'kind':'http','origin':'agent','release':release,'workflow_id_sha256':h,'state':'completed','at':104},{'kind':'cluster_status','origin':'agent','at':103,'roles':{'coordinator1':{'running':True,'container_sha256':ch}}}]}
  exec(compile(ast.Module(body=[node],type_ignores=[]),'stale_grader','exec'),ns);return ns
 def test_actual_completion_and_snapshot_must_precede_release(self):
  self.assertTrue(self.fixture()['stale']())
  for mutation in ['no_completed','late_completed','examiner_completed','late_database']:
   ns=self.fixture()
   if mutation=='no_completed':ns['obs'].pop(1)
   if mutation=='late_completed':ns['obs'][1]['at']=111
   if mutation=='examiner_completed':ns['obs'][1]['origin']='examiner'
   if mutation=='late_database':ns['obs'][0]['at']=111
   with self.subTest(mutation=mutation),self.assertRaises(AssertionError):ns['stale']()
 def test_takeover_epoch_and_same_live_container_are_required(self):
  for mutation in ['same_epoch','wrong_old_epoch','not_running','wrong_container','actual_die']:
   ns=self.fixture()
   if mutation=='same_epoch':ns['audit'][1]['epoch']=1
   if mutation=='wrong_old_epoch':ns['audit'][2]['epoch']=5
   if mutation=='not_running':ns['obs'][2]['roles']['coordinator1']['running']=False
   if mutation=='wrong_container':ns['obs'][2]['roles']['coordinator1']['container_sha256']='other'
   if mutation=='actual_die':ns['events']['containers'][0]['events']=[{'action':'die','at':105}]
   with self.subTest(mutation=mutation),self.assertRaises((AssertionError,StopIteration)):ns['stale']()
 def test_actual_held_claim_can_follow_an_earlier_same_coordinator_claim(self):
  ns=self.fixture();wid=ns['audit'][0]['workflow_id'];ns['audit']=[{'id':1,'workflow_id':wid,'task':'prepare','event':'claim','coordinator_id':'coordinator1','epoch':1},{'id':2,'workflow_id':wid,'task':'prepare','event':'claim','coordinator_id':'coordinator1','epoch':2},{'id':3,'workflow_id':wid,'task':'prepare','event':'claim','coordinator_id':'coordinator2','epoch':3},{'id':4,'workflow_id':wid,'task':'prepare','event':'settle','coordinator_id':'coordinator2','epoch':3},{'id':5,'workflow_id':wid,'task':'prepare','event':'stale_rejected','coordinator_id':'coordinator1','epoch':2}];ns['obs'][0]['claim_trace'][0]['id']=4;self.assertTrue(ns['stale']())
 def test_burst_requires_actual_stopped_states_and_bounded_native_controls(self):
  import copy
  source=ast.parse((SRC.parent/'level9-grade.py').read_text());node=next(x for x in ast.walk(source) if isinstance(x,ast.FunctionDef) and x.name=='burst_stopped_both');base={'burst':{'started_at':100,'finished_at':110,'coordinators_running_before':{'coordinator1':False,'coordinator2':False},'coordinators_running_after':{'coordinator1':False,'coordinator2':False}},'status':{'release':'workflow-v3'},'obs':[{'kind':'role_control','origin':'agent','release':'workflow-v3','action':'stop','roles':['coordinator1','coordinator2'],'at':90},{'kind':'role_control','origin':'agent','release':'workflow-v3','action':'start','roles':['coordinator1','coordinator2'],'at':120}]}
  for mutation in [None,'before_running','after_running','early_start','examiner_stop','wrong_release']:
   ns=copy.deepcopy(base)
   if mutation=='before_running':ns['burst']['coordinators_running_before']['coordinator1']=True
   if mutation=='after_running':ns['burst']['coordinators_running_after']['coordinator2']=True
   if mutation=='early_start':ns['obs'][1]['at']=105
   if mutation=='examiner_stop':ns['obs'][0]['origin']='examiner'
   if mutation=='wrong_release':ns['obs'][0]['release']='workflow-v3-r1'
   exec(compile(ast.Module(body=[node],type_ignores=[]),'burst_grader','exec'),ns)
   try:passed=ns['burst_stopped_both']()
   except AssertionError:passed=False
   with self.subTest(mutation=mutation):self.assertEqual(passed,mutation is None)
class ControllerPolicy(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);(self.root/'examiner-auth.key').write_bytes(b'x'*32)
  self.ns={'ROOT':self.root,'hmac':hmac,'hashlib':hashlib,'json':json,'re':__import__('re'),'time':types.SimpleNamespace(time=lambda:1,sleep=lambda _:None),'S':{'observations':[]},'save':lambda:None,'LAB':'fixture','LABEL':'id.harmonika.level9','NAMES':['sink','api','worker1','worker2','worker3','proxy','coordinator1','coordinator2']}
  nodes=[x for x in ast.parse((SRC/'controller.py').read_text()).body if isinstance(x,ast.FunctionDef)];exec(compile(ast.Module(body=nodes,type_ignores=[]),'controller','exec'),self.ns);self.ns['save']=lambda:None
 def tearDown(self):self.tmp.cleanup()
 def test_two_coordinators_internal_network_without_published_ports(self):
  calls=[];self.ns['S'].update(releases={'workflow-v3':{'image_id':'img'}},requests={},roles={},containers=[],network='owned-internal',volume='owned-data');self.ns['B']=types.SimpleNamespace(docker=lambda *a:calls.append(a) or 'cid'+str(len(calls)));self.ns['http']=lambda _: {'http_status':200};self.ns['activate']({'release':'workflow-v3','request_id':'stage-v3'});runs=[x for x in calls if x[0]=='run'];self.assertEqual(len(runs),8)
  for r in runs:
   self.assertNotIn('--publish',r);self.assertNotIn('-p',r);self.assertIn('--read-only',r);self.assertIn('ALL',r);role=next(x.split('=',1)[1] for x in r if x.startswith('COORDINATOR_ID='));network=r[r.index('--network')+1];self.assertEqual(network,'owned-internal' if role in ['api','proxy','coordinator1','coordinator2'] else 'none');self.assertIn('WORKFLOW_SCHEMA=3',r)
 def test_examiner_auth_cannot_be_forged_by_origin_marker(self):
  with self.assertRaises(ValueError):self.ns['authorize_request']({'op':'test-engine','origin':'examiner'})
  with self.assertRaises(ValueError):self.ns['authorize_request']({'op':'test-engine','origin':'agent'})
  body={'op':'status','origin':'examiner'};body['_operator_auth']=hmac.new(b'x'*32,json.dumps(body,sort_keys=True,separators=(',',':')).encode(),hashlib.sha256).hexdigest();self.ns['authorize_request'](body)
 def test_critical_proofs_survive_poll_ring(self):
  self.ns['record']({'kind':'database_witness','origin':'agent','claim_trace':[]})
  for _ in range(600):self.ns['record']({'kind':'http','status':200})
  self.assertEqual(len(self.ns['S']['observations']),512);self.assertEqual(len(self.ns['S']['critical_witnesses']),1)

class RunningSamplerControl(unittest.TestCase):
 def fixture(self,value,alive=True):
  import re
  node=next(x for x in ast.parse((SRC/'controller.py').read_text()).body if isinstance(x,ast.FunctionDef) and x.name=='observe_running');records=[];posts=[]
  ns={'json':json,'re':re,'hashlib':hashlib,'time':time,'S':{'roles':{'api':'owned-api'},'release':'workflow-v3-r1'},'B':types.SimpleNamespace(owned=lambda _:None,run=lambda *a,**kw:types.SimpleNamespace(stdout=json.dumps(value))),'inspect_role':lambda n:{'Id':'owned-worker','State':{'Running':alive},'RestartCount':0},'record':records.append,'http':lambda r:posts.append(r) or {'http_status':200}}
  exec(compile(ast.Module(body=[node],type_ignores=[]),'running_sampler','exec'),ns);return ns,records,posts
 def test_no_observation_never_issues_control(self):
  ns,records,posts=self.fixture({'observed':False,'reason':'hold_not_active'});v=ns['observe_running']({'workflow_id':'a'*32,'control':'cancel'});self.assertFalse(v['observed']);self.assertEqual(posts,[]);self.assertEqual(records[0]['kind'],'running_witness')
 def test_dead_worker_cannot_authorize_control(self):
  ns,records,posts=self.fixture({'observed':True,'worker_id':'worker1'},False);v=ns['observe_running']({'workflow_id':'a'*32,'control':'pause'});self.assertFalse(v['observed']);self.assertEqual(posts,[])
 def test_control_is_scoped_and_retains_actual_provenance(self):
  ns,records,posts=self.fixture({'observed':True,'worker_id':'worker1'});ns['observe_running']({'workflow_id':'a'*32,'owner':'beta','control':'pause','origin':'examiner'});self.assertEqual(posts,[{'method':'POST','path':'/workflows/'+'a'*32+'/pause','owner':'beta','body':{},'origin':'examiner'}]);self.assertEqual(records[0]['origin'],'examiner');self.assertNotIn('workflow_id',records[0])
  for r in [{'workflow_id':'../../host','control':'pause'},{'workflow_id':'a'*32,'owner':'unknown'},{'workflow_id':'a'*32,'control':'resume'}]:
   with self.subTest(request=r),self.assertRaises(ValueError):ns['observe_running'](r)

class BoundedCompleteAudit(unittest.TestCase):
 def run_snapshot(self,count):
  import contextlib,io
  code=next(n.value for n in ast.walk(ast.parse((SRC/'controller.py').read_text())) if isinstance(n,ast.Constant) and isinstance(n.value,str) and n.value.startswith('import sqlite3,json,hashlib\nout={}'));connect=sqlite3.connect
  with tempfile.TemporaryDirectory() as d:
   root=Path(d)
   for name in ['jobs','sink','mechanics','workflows','network']:
    with connect(root/(name+'.sqlite')) as c:
     c.execute('PRAGMA journal_mode=WAL');c.execute('PRAGMA user_version=3');c.execute('CREATE TABLE receipts(id INTEGER PRIMARY KEY, content BLOB)');c.executemany('INSERT INTO receipts(content) VALUES(?)',[(b'synthetic',)]*(count if name=='workflows' else 1))
   def scoped(path,*a,**kw):
    return connect(str(path).replace('file:/data/','file:'+str(root)+'/'),*a,**kw)
   out=io.StringIO()
   with patch('sqlite3.connect',scoped),contextlib.redirect_stdout(out):exec(compile(code,'bounded_audit','exec'),{})
   return json.loads(out.getvalue())
 def test_over_legacy_limit_is_complete_with_schema_and_counts(self):
  v=self.run_snapshot(1005)
  for d in v.values():self.assertEqual(d['user_version'],3);self.assertEqual(d['integrity'],'ok');self.assertEqual(d['journal_mode'],'wal')
  w=v['workflows'];self.assertEqual(w['table_counts']['receipts'],1005);self.assertEqual(len(w['tables']['receipts']),1005);self.assertEqual(w['tables']['receipts'][-1]['id'],1005);self.assertNotIn('synthetic',json.dumps(v))
 def test_exceeding_budget_fails_instead_of_silent_truncation(self):
  with self.assertRaisesRegex(RuntimeError,'audit_row_budget_exceeded'):self.run_snapshot(20001)

if __name__=='__main__':unittest.main(verbosity=2)
