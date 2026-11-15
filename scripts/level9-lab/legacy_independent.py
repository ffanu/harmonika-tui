"""Held-out synthetic WorkflowStore behavior, actual SQLite and callback boundaries."""
import base64,concurrent.futures,copy,hashlib,json,os,sqlite3,sys,tempfile,threading,time,unittest
from pathlib import Path
PROJECT=Path(os.environ['HARMONIKA_EXAM_PROJECT']);sys.path.insert(0,str(PROJECT));PHASE=os.environ.get('HARMONIKA_EXAM_PHASE','v2')
from workflow_engine import WorkflowStore
from workflow_adapter import Unavailable,OutcomeUnknown

def canonical(v):return json.dumps(v,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()
def request(text='fixture',**extra):return {'text':text,'tasks':[{'name':'prepare','needs':[]},{'name':'publish','needs':['prepare']}],**extra}
class Child:
 def __init__(self):self.jobs={};self.calls=[];self.ack=False;self.lookups_down=0;self.offline=False;self.corrupt=0;self.pending=False;self.full=0;self.lock=threading.Lock();self.enter=None;self.release=None
 def submit(self,owner,key,payload):
  self.calls.append(('submit',owner,key))
  if self.offline:raise OutcomeUnknown()
  if self.full:self.full-=1;return 429,{'error':'queue_full'}
  if self.enter:self.enter.set();self.release.wait(2)
  with self.lock:
   k=(owner,key)
   if k not in self.jobs:
    id=hashlib.sha256((owner+'\0'+key).encode()).hexdigest()[:32];self.jobs[k]={'id':id,'owner':owner,'key':key,'attempts':1,'worker_id':'fixture-worker','fence':1,'lease_until':0.0,'payload':copy.deepcopy(payload),'state':'running' if self.pending else 'completed'}
   job=self.jobs[k]
  if self.ack:self.ack=False;raise OutcomeUnknown()
  return 202,{'job':self.snap(job)}
 def snap(self,j):return {k:v for k,v in j.items() if k!='payload'}
 def lookup_request(self,owner,key):
  self.calls.append(('lookup',owner,key))
  if self.offline:raise Unavailable()
  if self.lookups_down:self.lookups_down-=1;return 503,{'error':'queue_busy'}
  j=self.jobs.get((owner,key));return (200,{'job':self.snap(j)}) if j else (404,{'error':'not_found'})
 def get(self,owner,id):
  if self.offline:raise Unavailable()
  j=next((j for j in self.jobs.values() if j['id']==id and j['owner']==owner),None);return (200,{'job':self.snap(j)}) if j else (404,{'error':'not_found'})
 def artifact(self,owner,id):
  self.calls.append(('artifact',owner,id))
  if self.offline:raise Unavailable()
  j=next(j for j in self.jobs.values() if j['id']==id and j['owner']==owner)
  if j['state']!='completed':return 409,b''
  if self.corrupt:self.corrupt-=1;return 200,b'CORRUPT_SYNTHETIC_ARTIFACT'
  return 200,canonical(j['payload'])
 def cancel(self,owner,id):
  self.calls.append(('cancel',owner,id));j=next(j for j in self.jobs.values() if j['id']==id and j['owner']==owner);j['state']='cancelled';return 200,{'job':self.snap(j)}
 def health(self):
  if self.offline:raise Unavailable()

class Base(unittest.TestCase):
 def setUp(self):self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)/'workflows.sqlite';self.child=Child();self.stores=[];self.q=self.store()
 def tearDown(self):
  for q in self.stores:q.stop()
  self.tmp.cleanup()
 def store(self,**kw):
  q=WorkflowStore(str(self.path),self.child,**kw);self.stores.append(q);return q
 def submit(self,key='one',owner='alpha',data=None):
  status,value=self.q.submit(owner,key,request() if data is None else data);self.assertEqual(status,202);return value['workflow']['id']
 def pump(self,id,owner='alpha',q=None,limit=240):
  q=q or self.q
  for _ in range(limit):
   q.tick();v=q.get(owner,id)[1]['workflow']
   if v['state'] in ['completed','cancelled']:return v
   time.sleep(.025)
  self.fail('workflow_did_not_settle')
 def rows(self,table):
  with sqlite3.connect(self.path) as c:c.row_factory=sqlite3.Row;return [dict(x) for x in c.execute('SELECT * FROM '+table)]
class Workflow(Base):
 def test_01_atomic_validation_graphs(self):
  invalid=[{},request(text=''),request(tasks=[]),request(tasks=[{'name':'a','needs':['missing']}]),request(tasks=[{'name':'a','needs':['a']}]),request(tasks=[{'name':'a','needs':['b']},{'name':'b','needs':['a']}]),request(tasks=[{'name':'a','needs':[]},{'name':'a','needs':[]}]),request(tasks=[{'name':'A','needs':[]}]),request(extra=True)]
  for p in invalid:self.assertEqual(self.q.submit('alpha','invalid',p)[0],422)
  self.assertEqual(self.rows('workflows'),[]);self.assertEqual(self.rows('steps'),[]);self.assertEqual(self.rows('events'),[])
 def test_02_owner_request_and_conflict(self):
  id=self.submit();self.assertEqual(self.q.submit('alpha','one',request())[1]['workflow']['id'],id);self.assertEqual(self.q.submit('alpha','one',request('changed'))[0],409)
  self.assertEqual(self.q.get('beta',id)[0],404);self.assertEqual(self.q.cancel('beta',id)[0],404);self.assertEqual(self.q.artifact('beta',id)[0],404);self.assertEqual(self.q.lookup_request('beta','one')[0],404)
 def test_03_global_capacity_and_replay(self):
  for i in range(4):self.submit(str(i))
  other=self.store();self.assertEqual(other.submit('alpha','five',request())[0],429);self.assertEqual(other.submit('alpha','0',request())[0],200);self.assertEqual(other.submit('beta','0',request())[0],202)
 def test_04_concurrent_admission_atomic(self):
  qs=[self.store() for _ in range(10)];barrier=threading.Barrier(10)
  def submit(i):barrier.wait();return qs[i].submit('alpha','c'+str(i),request())[0]
  with concurrent.futures.ThreadPoolExecutor(max_workers=10) as pool:statuses=list(pool.map(submit,range(10)))
  self.assertEqual(statuses.count(202),4);self.assertTrue(set(statuses)<={202,429,503});self.assertEqual(len(self.rows('workflows')),4)
 def test_05_dag_content_and_result_bytes(self):
  p=request('α',tasks=[{'name':'finish','needs':['right','left']},{'name':'right','needs':['root']},{'name':'root','needs':[]},{'name':'left','needs':['root']}]);id=self.submit(data=p);v=self.pump(id);self.assertEqual(v['state'],'completed');expected=[]
  for task in v['tasks']:
   key='w'+hashlib.sha256(('alpha\0'+id+'\0'+task['name']).encode()).hexdigest()[:48];self.assertEqual(task['request_key'],key);j=self.child.jobs[('alpha',key)];needs=task['needs']
   digest=hashlib.sha256(canonical([{'name':n,'sha256':next(x['sha256'] for x in v['tasks'] if x['name']==n)} for n in sorted(needs)])).hexdigest();text=task['name']+':'+(digest if needs else p['text']);raw=canonical({'kind':'report','text':text});self.assertEqual(j['payload'],json.loads(raw));self.assertEqual(task['sha256'],hashlib.sha256(raw).hexdigest());expected.append({'name':task['name'],'sha256':task['sha256']})
  self.assertEqual(self.q.artifact('alpha',id),(200,canonical({'workflow_id':id,'tasks':sorted(expected,key=lambda x:x['name'])})))
 def test_06_unknown_ack_lookup_before_retry(self):
  self.child.ack=True;self.child.lookups_down=2;id=self.submit();self.q.tick();q2=self.store();v=self.pump(id,q=q2);self.assertEqual(v['state'],'completed');self.assertEqual(sum(c[0]=='submit' for c in self.child.calls),2);self.assertGreaterEqual(sum(c[0]=='lookup' for c in self.child.calls),3)
 def test_07_wrong_artifact_reconciled_without_resubmit(self):
  self.child.corrupt=1;id=self.submit();self.pump(id);self.assertEqual(sum(c[0]=='submit' for c in self.child.calls),2);self.assertGreaterEqual(sum(c[0]=='artifact' for c in self.child.calls),3)
 def test_08_offline_is_not_success_and_recovers(self):
  self.child.offline=True;id=self.submit();self.q.tick();self.assertNotEqual(self.q.get('alpha',id)[1]['workflow']['state'],'completed');self.assertEqual(self.q.artifact('alpha',id)[0],409);self.child.offline=False;self.pump(id)
 def test_09_cancel_before_dispatch_no_child_calls(self):
  id=self.submit();self.assertEqual(self.q.cancel('alpha',id)[1]['workflow']['state'],'cancelled');self.q.tick();self.assertEqual(self.child.calls,[]);self.assertEqual(self.q.artifact('alpha',id)[0],409)
 def test_10_cancel_existing_child_no_descendant(self):
  self.child.pending=True;id=self.submit();self.q.tick();self.assertTrue(self.child.jobs);self.q.cancel('alpha',id);v=self.pump(id);self.assertEqual(v['state'],'cancelled');self.assertEqual(sum(c[0]=='submit' for c in self.child.calls),1);self.assertEqual(self.q.artifact('alpha',id)[0],409)
 def test_11_sqlite_busy_no_partial_writes(self):
  c=sqlite3.connect(self.path);c.execute('BEGIN IMMEDIATE');start=time.monotonic()
  try:status,_=self.q.submit('alpha','busy',request())
  finally:c.rollback();c.close()
  self.assertEqual(status,503);self.assertLess(time.monotonic()-start,.8);self.assertEqual(self.rows('workflows'),[]);self.assertEqual(self.rows('events'),[])
 def test_12_no_write_lock_during_child_io(self):
  self.child.enter=threading.Event();self.child.release=threading.Event();id=self.submit();errors=[]
  def tick():
   try:self.q.tick()
   except Exception as e:errors.append(type(e).__name__)
  t=threading.Thread(target=tick);t.start();self.assertTrue(self.child.enter.wait(2))
  try:
   c=sqlite3.connect(self.path,timeout=.3);c.execute('BEGIN IMMEDIATE');c.rollback();c.close()
  finally:self.child.release.set();t.join(3)
  self.assertFalse(errors);self.assertFalse(t.is_alive())
 def test_13_restart_preserves_checkpoint(self):
  id=self.submit();self.pump(id);before=self.q.artifact('alpha',id);q2=self.store();q2.tick();self.assertEqual(q2.artifact('alpha',id),before);self.assertEqual(sum(c[0]=='submit' for c in self.child.calls),2)
 def test_20_failed_child_blocks_descendant_but_allows_cancel(self):
  self.child.pending=True;id=self.submit();self.q.tick()
  for j in self.child.jobs.values():j['state']='failed'
  self.q.tick();self.assertNotEqual(self.q.get('alpha',id)[1]['workflow']['state'],'completed');self.q.cancel('alpha',id);v=self.pump(id);self.assertEqual(v['state'],'cancelled');self.assertEqual(sum(c[0]=='submit' for c in self.child.calls),1)
 def test_21_child_backpressure_keeps_same_key_and_progresses(self):
  self.child.full=2;id=self.submit();self.pump(id);self.assertEqual(len(self.child.jobs),2);keys=[c[2] for c in self.child.calls if c[0]=='submit'];self.assertEqual(len(set(keys)),2)
 def test_22_snapshots_exclude_input_and_exception_data(self):
  id=self.submit(data=request('PRIVATE_DATA_CANARY'));value=self.q.get('alpha',id)[1]['workflow'];self.assertTrue({'id','owner','key','state','tasks'}<=set(value));self.assertFalse(set(value)&{'payload','text','input','exception','content','prompt'});self.assertNotIn('PRIVATE_DATA_CANARY',json.dumps(value))
 def test_14_event_cursor_private_and_idempotent(self):
  id=self.submit(data=request('PRIVATE_DATA_CANARY'));self.pump(id);events=self.q.events('alpha',0)[1]['events'];self.assertTrue(events);self.assertEqual([e['id'] for e in events],sorted({e['id'] for e in events}));self.assertTrue(all(set(e)=={'id','workflow_id','task','state'} for e in events));self.assertNotIn('PRIVATE_DATA_CANARY',json.dumps(events));self.assertEqual(self.q.events('beta',0)[1]['events'],[]);self.assertEqual(self.q.events('alpha',True)[0],422);last=events[-1]['id'];self.q.tick();self.assertEqual(self.q.events('alpha',last)[1]['events'],[])

@unittest.skipUnless(PHASE=='v2','turn_b_only')
class Upgrade(Base):
 # Inherit fixtures, not core test methods: only run five v2 checks below.
 def test_15_migration_failure_atomic_then_success(self):
  id=self.submit();self.pump(id);artifact=self.q.artifact('alpha',id)
  def fail(point):self.assertEqual(point,'after_priority_column');raise RuntimeError('injected_migration_failure')
  with self.assertRaises(RuntimeError):self.store(schema_version=2,migration_hook=fail)
  with sqlite3.connect(self.path) as c:self.assertEqual(c.execute('PRAGMA user_version').fetchone()[0],1);self.assertNotIn('priority',{x[1] for x in c.execute('PRAGMA table_info(workflows)')})
  fresh=Path(self.tmp.name)/'fresh.sqlite'
  with self.assertRaises(RuntimeError):WorkflowStore(str(fresh),self.child,schema_version=2,migration_hook=fail)
  with sqlite3.connect(fresh) as c:self.assertEqual(c.execute('PRAGMA user_version').fetchone()[0],0);self.assertEqual(c.execute("SELECT count(*) FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchone()[0],0)
  v2=self.store(schema_version=2);self.assertEqual(v2.submit('alpha','one',request(priority=0))[0],409);self.assertEqual(v2.get('alpha',id)[1]['workflow']['priority'],0);self.assertFalse(v2.get('alpha',id)[1]['workflow']['paused']);self.assertEqual(v2.artifact('alpha',id),artifact);self.assertEqual(v2.submit('alpha','one',request())[0],200)
 def test_16_priority_order_and_validation(self):
  self.q=self.store(schema_version=2)
  for bad in [True,-1,10,'9']:self.assertEqual(self.q.submit('alpha','bad',request(priority=bad))[0],422)
  low=self.submit('low',data=request(priority=0));high=self.submit('high',data=request(priority=9));self.q.tick();self.assertTrue(self.child.calls);first=next(x for x in self.child.calls if x[0]=='submit');self.assertEqual(first[2],self.q.get('alpha',high)[1]['workflow']['tasks'][0]['request_key'])
 def test_17_pause_reconciles_reserved_child_without_dispatching_descendant(self):
  self.q=self.store(schema_version=2);self.child.pending=True;id=self.submit();self.q.tick();self.assertEqual(self.q.pause('alpha',id)[0],200)
  for j in self.child.jobs.values():j['state']='completed'
  self.q.tick();q2=self.store(schema_version=2);self.assertTrue(q2.get('alpha',id)[1]['workflow']['paused']);self.assertEqual(sum(c[0]=='submit' for c in self.child.calls),1);self.assertEqual(q2.pause('beta',id)[0],404);q2.resume('alpha',id);self.child.pending=False;self.pump(id,q=q2);self.assertEqual(sum(c[0]=='submit' for c in self.child.calls),2)
 def test_18_real_migration_lock_preserves_v1(self):
  c=sqlite3.connect(self.path);c.execute('BEGIN IMMEDIATE');start=time.monotonic()
  try:
   with self.assertRaises(Exception):self.store(schema_version=2)
  finally:c.rollback();c.close()
  self.assertLess(time.monotonic()-start,.8)
  with sqlite3.connect(self.path) as c:self.assertEqual(c.execute('PRAGMA user_version').fetchone()[0],1)
  self.store(schema_version=2)
 def test_19_repeated_v2_and_old_v1_never_downgrade(self):
  id=self.submit();self.pump(id);self.store(schema_version=2);hook_calls=[];self.store(schema_version=2,migration_hook=lambda p:hook_calls.append(p));self.assertEqual(hook_calls,[]);old=self.store(schema_version=1);self.assertEqual(old.get('alpha',id)[0],200)
  with sqlite3.connect(self.path) as c:self.assertEqual(c.execute('PRAGMA user_version').fetchone()[0],2)
if __name__=='__main__':unittest.main(verbosity=2)
