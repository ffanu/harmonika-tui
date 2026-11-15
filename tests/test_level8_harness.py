import ast,hashlib,hmac,importlib.util,json,os,socket,sqlite3,sys,tempfile,threading,types,unittest
from pathlib import Path
from http.server import BaseHTTPRequestHandler
from socketserver import ThreadingUnixStreamServer
SRC=Path(__file__).resolve().parents[1]/'scripts/level8-lab';sys.path.insert(0,str(SRC))
import fault_store,workflow_adapter
class Faults(unittest.TestCase):
 def setUp(self):self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);(self.root/'faults').mkdir();self.old=fault_store.ROOT;fault_store.ROOT=self.root
 def tearDown(self):fault_store.ROOT=self.old;self.tmp.cleanup()
 def arm(self,kind):(self.root/'faults'/kind).touch()
 def test_fault_only_once_after_repeated_take(self):self.arm('once');self.assertTrue(fault_store.take('once'));self.assertFalse(fault_store.take('once'))
 def test_lookup_failures_only_match_committed_target(self):
  self.arm('child-submit-ack-loss');self.arm('child-lookup-fail-two');fault_store.take('child-submit-ack-loss','alpha','stable','job');self.assertFalse(fault_store.lookup_fault('beta','stable'));self.assertFalse(fault_store.lookup_fault('alpha','other'));self.assertTrue(fault_store.lookup_fault('alpha','stable'));self.assertTrue(fault_store.lookup_fault('alpha','stable'));self.assertFalse(fault_store.lookup_fault('alpha','stable'))
 def test_audit_contains_only_mechanics_and_hashed_key(self):
  fault_store.audit('child_submit','alpha','PRIVATE_DATA_CANARY','job',202)
  with fault_store.connect() as c:row=dict(c.execute('SELECT * FROM calls').fetchone())
  self.assertNotIn('PRIVATE_DATA_CANARY',json.dumps(row));self.assertEqual(set(row),{'id','kind','owner','key_sha256','job_id','status','at','release'});self.assertEqual(row['key_sha256'],fault_store.digest('alpha','PRIVATE_DATA_CANARY'))
 def test_new_release_has_fresh_budget_without_resetting_old_receipts(self):
  from unittest.mock import patch
  self.arm('once')
  with patch.dict(os.environ,{'LAB_RELEASE':'workflow-v2'}):self.assertTrue(fault_store.take('once'));self.assertFalse(fault_store.take('once'))
  with patch.dict(os.environ,{'LAB_RELEASE':'workflow-v2-r1'}):self.assertTrue(fault_store.take('once'));self.assertFalse(fault_store.take('once'))
  with patch.dict(os.environ,{'LAB_RELEASE':'workflow-v2'}):self.assertFalse(fault_store.take('once'))
  with fault_store.connect() as c:self.assertEqual(c.execute('SELECT count(*) FROM spent').fetchone()[0],2)
class Controller(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);(self.root/'examiner-auth.key').write_bytes(b'x'*32)
  self.ns={'ROOT':self.root,'hmac':hmac,'S':{'observations':[]},'save':lambda:None,'hashlib':hashlib,'json':json,'time':types.SimpleNamespace(time=lambda:1,sleep=lambda _:None),'re':__import__('re'),'NAMES':['sink','api','worker1','worker2','worker3','coordinator'],'LAB':'fixture','LABEL':'fixture.label'}
  for node in ast.parse((SRC/'controller.py').read_text()).body:
   if isinstance(node,ast.FunctionDef):exec(compile(ast.Module(body=[node],type_ignores=[]),'controller_fixture','exec'),self.ns)
  self.ns['save']=lambda:None
 def tearDown(self):self.tmp.cleanup()
 def test_examiner_origin_requires_valid_request_mac(self):
  body={'origin':'examiner','op':'status'}
  with self.assertRaises(ValueError):self.ns['authorize_request'](body)
  body['_operator_auth']=hmac.new(b'x'*32,json.dumps(body,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode(),hashlib.sha256).hexdigest();self.ns['authorize_request'](body);body['op']='capture-v1'
  with self.assertRaises(ValueError):self.ns['authorize_request'](body)
 def test_agent_cannot_run_examiner_only_operations(self):
  self.ns['authorize_request']({'origin':'agent','op':'http'})
  for op in ['capture-v1','test-engine']:
   with self.assertRaises(ValueError):self.ns['authorize_request']({'origin':'agent','op':op})
 def test_pause_witness_survives_poll_ring(self):
  self.ns['record']({'kind':'http','paused':True,'state':'running'})
  for _ in range(600):self.ns['record']({'kind':'http','status':200})
  self.assertEqual(len(self.ns['S']['observations']),512);self.assertEqual(len(self.ns['S']['critical_witnesses']),1)
 def test_unready_six_role_cluster_has_no_activation_success(self):
  calls=[];self.ns['S'].update(releases={'workflow-v2':{'image_id':'image'}},requests={},roles={},containers=[],volume='fixture');self.ns['B']=types.SimpleNamespace(docker=lambda *args:calls.append(args) or 'cid'+str(len(calls)));self.ns['http']=lambda _: {'http_status':503};value=self.ns['activate']({'release':'workflow-v2','request_id':'stage-v2'});self.assertEqual(value['exit_code'],70);self.assertNotIn('stage-v2',self.ns['S']['requests']);self.assertEqual(sum(c[0]=='run' for c in calls),6);self.assertTrue(all('WORKFLOW_SCHEMA=2' in c and 'LAB_RELEASE=workflow-v2' in c for c in calls if c[0]=='run'));self.assertEqual(sum('LAB_ROLE=coordinator' in c for c in calls),1)
 def test_docker_event_archive_survives_daemon_eviction(self):
  self.ns['S'].update(created_at=1,role_history={'cid':{'role':'coordinator'}});responses=[json.dumps({'Actor':{'ID':'cid','Attributes':{'exitCode':'42'}},'Action':'die','time':2}),''];self.ns['B']=types.SimpleNamespace(docker=lambda *args:responses.pop(0),owned=lambda _:None);self.ns['capture_events']();self.ns['capture_events']();self.assertEqual(self.ns['S']['docker_event_archive']['cid'],[{'action':'die','at':2,'exit_code':'42'}])
 def test_test_engine_failure_is_data_not_transport_failure(self):self.assertEqual(self.ns['operation_exit_code']({'op':'test-engine'},{'exit_code':1}),0)
class MockProtocol(unittest.TestCase):
 def test_child_mock_uses_actual_queue_uuid_shape(self):
  import copy
  ns={'copy':copy,'hashlib':hashlib,'threading':threading,'Unavailable':workflow_adapter.Unavailable,'OutcomeUnknown':workflow_adapter.OutcomeUnknown,'json':json}
  for node in ast.parse((SRC/'independent.py').read_text()).body:
   if isinstance(node,ast.FunctionDef) and node.name=='canonical' or isinstance(node,ast.ClassDef) and node.name=='Child':exec(compile(ast.Module(body=[node],type_ignores=[]),'independent_fixture','exec'),ns)
  child=ns['Child']();status,value=child.submit('alpha','stable',{'kind':'report','text':'synthetic'});self.assertEqual(status,202);self.assertRegex(value['job']['id'],r'^[a-f0-9]{32}$');self.assertEqual(value['job']['key'],'stable')
class GradingProtocol(unittest.TestCase):
 def setUp(self):
  spec=importlib.util.spec_from_file_location('level8_grade_protocol',SRC.parent/'level8-grade.py');self.g=importlib.util.module_from_spec(spec);spec.loader.exec_module(self.g)
 def test_fenced_rejections_are_not_effects_and_duplicate_success_still_fails(self):
  import copy
  db={'jobs':{'tables':{'jobs':[{'id':'j','owner':'alpha','state':'completed'}]}},'sink':{'tables':{'effects':[{'job_id':'j','owner':'alpha','fence':3}],'calls':[{'id':1,'job_id':'j','worker_id':'worker1','fence':3,'kind':'authorize'},{'id':2,'job_id':'j','worker_id':'worker2','fence':1,'kind':'execute'},{'id':3,'job_id':'j','worker_id':'worker2','fence':1,'kind':'stale_rejected'},{'id':4,'job_id':'j','worker_id':'worker1','fence':3,'kind':'execute'}]}}}
  self.assertTrue(self.g.effect_proof(db))
  for mutation in ['duplicate_effect','missing_rejection','duplicate_success','orphan_effect']:
   bad=copy.deepcopy(db)
   if mutation=='duplicate_effect':bad['sink']['tables']['effects']*=2
   if mutation=='missing_rejection':bad['sink']['tables']['calls'].pop(2)
   if mutation=='duplicate_success':bad['sink']['tables']['calls'].append({'id':5,'job_id':'j','worker_id':'worker1','fence':3,'kind':'execute'})
   if mutation=='orphan_effect':bad['sink']['tables']['effects'][0]['job_id']='unknown'
   with self.subTest(mutation=mutation),self.assertRaises(AssertionError):self.g.effect_proof(bad)
 def test_status_probes_require_actual_agent_parent_and_same_offline_window(self):
  import copy
  obs=[{'kind':'cluster_status','origin':'agent','at':10,'roles':{'sink':{'running':False},'coordinator':{'running':True}}},{'kind':'http','origin':'controller','method':'GET','route':'/healthz','status':200,'at':10.2,'release':'workflow-v2'},{'kind':'http','origin':'controller','method':'GET','route':'/readyz','status':503,'at':10.4,'release':'workflow-v2'}]
  self.assertTrue(self.g.offline_probe_pair(obs,9,12,'workflow-v2'))
  self.assertFalse(self.g.offline_probe_pair(obs[1:],9,12,'workflow-v2'))
  self.assertFalse(self.g.offline_probe_pair(obs,11,12,'workflow-v2'))
  self.assertFalse(self.g.offline_probe_pair(obs,9,12,'workflow-v2-r1'))
  bad=copy.deepcopy(obs);bad[0]['roles']['sink']['running']=True;self.assertFalse(self.g.offline_probe_pair(bad,9,12,'workflow-v2'))
  direct=copy.deepcopy(obs[1:]);direct[0]['origin']=direct[1]['origin']='agent';direct[1]['at']=15;self.assertTrue(self.g.offline_probe_pair(direct,9,16,'workflow-v2'))
class Adapter(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory(prefix='l8-',dir='/tmp');self.root=Path(self.tmp.name);(self.root/'faults').mkdir();self.old=fault_store.ROOT;fault_store.ROOT=self.root;self.mode='normal';self.commits=0;test=self
  class Handler(BaseHTTPRequestHandler):
   protocol_version='HTTP/1.1'
   def log_message(self,*a):pass
   def do_POST(self):
    self.rfile.read(int(self.headers['Content-Length']));test.commits+=1
    if test.mode=='drop':self.close_connection=True;self.connection.shutdown(socket.SHUT_RDWR);self.connection.close();return
    self.respond(202,b'{"job":{"id":"j","owner":"alpha","state":"queued"}}')
   def do_GET(self):self.respond(200,b'{"kind":"report","text":"synthetic"}')
   def respond(self,status,raw):self.send_response(status);self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
  class Server(ThreadingUnixStreamServer):daemon_threads=True
  self.server=Server(str(self.root/'queue.sock'),Handler);self.thread=threading.Thread(target=self.server.serve_forever);self.thread.start();self.client=workflow_adapter.ChildClient(self.root)
 def tearDown(self):self.server.shutdown();self.server.server_close();self.thread.join(2);fault_store.ROOT=self.old;self.tmp.cleanup()
 def test_real_unix_http_submit_and_artifact_bytes(self):
  status,value=self.client.submit('alpha','stable',{'kind':'report','text':'synthetic'});self.assertEqual(status,202);self.assertEqual(value['job']['id'],'j');self.assertEqual(self.commits,1);self.assertEqual(self.client.artifact('alpha','j'),(200,b'{"kind":"report","text":"synthetic"}'))
 def test_real_connection_drop_after_commit_is_unknown(self):
  self.mode='drop'
  with self.assertRaises(workflow_adapter.OutcomeUnknown) as caught:self.client.submit('alpha','stable',{'kind':'report','text':'synthetic'})
  self.assertEqual(self.commits,1);self.assertEqual(str(caught.exception),'')
 def test_unavailable_socket_is_not_false_result(self):
  client=workflow_adapter.ChildClient(self.root/'missing')
  with self.assertRaises(workflow_adapter.Unavailable):client.get('alpha','j')
if __name__=='__main__':unittest.main()
