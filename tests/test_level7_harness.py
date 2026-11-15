import base64,hashlib,importlib.util,json,sqlite3,tempfile,unittest
from pathlib import Path
SRC=Path(__file__).resolve().parents[1]/'scripts/level7-lab'
class SinkContract(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);spec=importlib.util.spec_from_file_location('level7_sink_fixture',SRC/'effect_service.py');self.s=importlib.util.module_from_spec(spec);spec.loader.exec_module(self.s);self.s.initialize(self.root)
 def tearDown(self):self.tmp.cleanup()
 def op(self,op,**kw):return self.s.operate(op,{'job_id':'j','worker_id':'worker1','fence':1,**kw})
 def test_fence_rejects_old_live_worker(self):
  self.assertEqual(self.op('authorize',owner='alpha')[0],200);self.assertEqual(self.op('authorize',owner='alpha',fence=2)[0],200);self.assertEqual(self.op('execute',payload={'kind':'report','text':'x'})[0],409);status,v,_=self.op('execute',fence=2,payload={'kind':'report','text':'x'});self.assertEqual(status,200);self.assertEqual(v['fence'],2)
  with self.s.db() as c:self.assertEqual(c.execute('SELECT count(*) FROM effects').fetchone()[0],1);self.assertEqual(c.execute("SELECT count(*) FROM calls WHERE kind='stale_rejected'").fetchone()[0],1)
 def test_effect_idempotency(self):
  self.op('authorize',owner='alpha');p={'kind':'report','text':'α'};a=self.op('execute',payload=p);b=self.op('execute',payload=p);self.assertEqual(a,b);self.assertEqual(self.op('execute',payload={'kind':'report','text':'other'})[0],409)
 def test_owner_cannot_change_at_higher_fence(self):
  self.op('authorize',owner='alpha');self.assertEqual(self.op('authorize',owner='beta',fence=2)[0],409)
 def test_unknown_ack_effect_commits_before_drop_marker(self):
  (self.root/'faults/unknown-ack').touch();self.op('authorize',owner='alpha');status,value,fault=self.op('execute',payload={'kind':'report','text':'x'});self.assertEqual(fault,'unknown-ack');self.assertEqual(self.op('lookup')[1],value)
  with sqlite3.connect(self.root/'sink.sqlite') as c:self.assertEqual(c.execute("SELECT n FROM spent WHERE kind='unknown-ack'").fetchone()[0],1)
 def test_two_lookup_faults_are_target_bound(self):
  for f in ['lookup-fail-two','unknown-ack']:(self.root/('faults/'+f)).touch()
  self.op('authorize',owner='alpha');self.op('execute',payload={'kind':'report','text':'x'});self.assertEqual(self.op('lookup',job_id='unrelated')[0],200);self.assertEqual(self.op('lookup')[0],503);self.assertEqual(self.op('lookup')[0],503);self.assertEqual(self.op('lookup')[0],200)
 def test_bad_receipt_does_not_corrupt_stored_effect(self):
  (self.root/'faults/bad-receipt').touch();self.op('authorize',owner='alpha');_,bad,fault=self.op('execute',payload={'kind':'report','text':'x'});self.assertEqual(fault,'bad-receipt');_,good,_=self.op('lookup');self.assertNotEqual(bad['sha256'],good['sha256']);self.assertEqual(hashlib.sha256(base64.b64decode(good['content_b64'])).hexdigest(),good['sha256'])
 def test_crash_instruction_only_after_receipt_commits(self):
  (self.root/'faults/crash-after-effect').touch();self.op('authorize',owner='alpha');_,value,fault=self.op('execute',payload={'kind':'report','text':'x'});self.assertEqual(fault,'crash-after-effect');self.assertEqual(self.op('lookup')[1],value);self.assertIsNone(self.op('execute',payload={'kind':'report','text':'x'})[2])
 def test_calls_are_mechanical(self):
  self.op('authorize',owner='alpha');self.op('execute',payload={'kind':'report','text':'PRIVATE_SYNTHETIC_CANARY'})
  with self.s.db() as c:
   rows=[dict(x) for x in c.execute('SELECT * FROM calls')];self.assertNotIn('PRIVATE_SYNTHETIC_CANARY',json.dumps(rows));self.assertEqual(set(rows[0]),{'id','job_id','worker_id','fence','kind'})

class ControllerGuards(unittest.TestCase):
 def setUp(self):
  import ast,types
  tree=ast.parse((SRC/'controller.py').read_text());self.ns={'S':{'observations':[]},'save':lambda:None,'hashlib':hashlib,'json':json,'base64':base64,'re':__import__('re'),'time':types.SimpleNamespace(time=lambda:1,sleep=lambda _:None),'NAMES':['sink','api','worker1','worker2','worker3'],'LAB':'fixture','LABEL':'fixture.label'}
  for node in tree.body:
   if isinstance(node,ast.FunctionDef):exec(compile(ast.Module(body=[node],type_ignores=[]),'controller_fixture','exec'),self.ns)
  self.ns['save']=lambda:None
 def test_observed_docker_events_survive_daemon_buffer_eviction(self):
  self.ns['S']['role_history']={'worker-cid':{'role':'worker3'}};event={'action':'die','at':123,'exit_code':'42'}
  self.assertEqual(self.ns['merge_docker_events']('worker-cid',[event]),[event]);self.assertEqual(self.ns['merge_docker_events']('worker-cid',[]),[event]);self.assertEqual(self.ns['merge_docker_events']('worker-cid',[event]),[event])
 def test_unregistered_container_cannot_enter_event_archive(self):
  with self.assertRaises(ValueError):self.ns['merge_docker_events']('unregistered',[{'action':'die','at':1,'exit_code':'42'}])
 def test_nonzero_image_tests_remain_structured_result(self):self.assertEqual(self.ns['operation_exit_code']({'op':'test-engine'},{'exit_code':1}),0)
 def test_lost_ack_retains_exit75(self):self.assertEqual(self.ns['operation_exit_code']({'op':'http'},{'exit_code':75}),75)
 def test_fault_witness_survives_poll_eviction(self):
  record=self.ns['record'];record({'kind':'fault','fault':'release-worker'})
  for _ in range(600):record({'kind':'http','status':200})
  self.assertEqual(len(self.ns['S']['observations']),512);self.assertEqual(self.ns['S']['critical_witnesses'],[{'kind':'fault','fault':'release-worker'}])
 def test_critical_budget_fails_instead_of_evicting_proof(self):
  self.ns['S']['critical_witnesses']=[{'kind':'fault'}]*4096
  with self.assertRaises(RuntimeError):self.ns['record']({'kind':'fault'})
  self.assertEqual(len(self.ns['S']['critical_witnesses']),4096)
 def test_drop_ack_arms_until_successful_commit_response(self):
  import types
  status=[503];body=[{'error':'queue_busy'}]
  self.ns['S'].update(roles={'api':'api'},drop_next_submit=True)
  self.ns['B']=types.SimpleNamespace(owned=lambda _:None,HTTP='',run=lambda *a,**kw:types.SimpleNamespace(returncode=0,stdout=json.dumps({'http_status':status[0],'body_b64':base64.b64encode(json.dumps(body[0]).encode()).decode()})))
  r={'path':'/jobs','method':'POST','key':'stable','body':{'kind':'report','text':'fixture'}}
  self.assertEqual(self.ns['http'](r)['http_status'],503);self.assertTrue(self.ns['S']['drop_next_submit'])
  status[0]=202;body[0]={'job':{'id':'j','state':'queued'}};self.assertEqual(self.ns['http'](r)['exit_code'],75);self.assertNotIn('drop_next_submit',self.ns['S']);self.assertEqual(len(self.ns['S']['dropped_submit_receipts']),1)
 def test_full_history_scan_detects_old_container_leak(self):
  import types
  self.ns['S'].update(roles={},role_history={'old':{'role':'api','release':'old'},'new':{'role':'api','release':'new'}})
  self.ns['B']=types.SimpleNamespace(owned=lambda _:None,run=lambda args:types.SimpleNamespace(returncode=0,stdout='PRIVATE_DATA_CANARY'+'\nbenign'*100 if args[-1]=='old' else '',stderr=''))
  scans=self.ns['operation']({'op':'logs'})['complete_history_scans'];self.assertEqual(len(scans),2);self.assertTrue(scans[0]['canary_present']);self.assertFalse(scans[1]['canary_present'])
 def test_unready_activation_cannot_record_success(self):
  import types
  self.ns['S'].update(releases={'cluster-v2':{'image_id':'image'}},requests={},roles={},containers=[],volume='fixture-volume')
  self.ns['B']=types.SimpleNamespace(docker=lambda *a:'cid-'+str(len(self.ns['S']['containers'])))
  self.ns['http']=lambda _: {'http_status':503}
  result=self.ns['activate']({'release':'cluster-v2','request_id':'stage-v2'});self.assertEqual(result['exit_code'],70);self.assertNotIn('stage-v2',self.ns['S']['requests'])

class GradingFailureEvidence(unittest.TestCase):
 def invoke(self, root):
  import subprocess,sys
  return subprocess.run([sys.executable,str(SRC.parent/'level7-grade.py'),'--root',str(root),'--phase','v2','--label','fixture'],capture_output=True,text=True,timeout=10)
 def test_missing_configuration_preserves_explicit_failed_result(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);result=self.invoke(root);self.assertEqual(result.returncode,1);value=json.loads((root/'fixture-result.json').read_text());self.assertFalse(value['passed']);self.assertEqual(value['failures'],['grading_interrupted']);self.assertEqual(value['errors'],{'grading_interrupted':'FileNotFoundError'})
 def test_existing_result_is_never_overwritten(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);p=root/'fixture-result.json';p.write_bytes(b'preserve-original-proof');result=self.invoke(root);self.assertEqual(result.returncode,1);self.assertEqual(p.read_bytes(),b'preserve-original-proof')

if __name__=='__main__':unittest.main()
