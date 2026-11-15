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
class HighAvailability(Base):
 def store(self,**kw):
  kw.setdefault('schema_version',3);kw.setdefault('coordinator_id','c'+str(len(self.stores)+1));return super().store(**kw)
 def test_23_schema3_additive_failure_and_legacy_checkpoint(self):
  with tempfile.TemporaryDirectory() as td:
   path=Path(td)/'wf.sqlite';old=WorkflowStore(path,self.child,schema_version=2);status,v=old.submit('alpha','old',request());wid=v['workflow']['id'];self.pump(wid,q=old);artifact=old.artifact('alpha',wid)
   with sqlite3.connect(path) as c:before=c.iterdump();before='\n'.join(before)
   def fail(stage):
    if stage=='after_claim_owner':raise RuntimeError('PRIVATE_ERROR_CANARY')
   with self.assertRaises(RuntimeError):WorkflowStore(path,self.child,schema_version=3,migration_hook=fail)
   with sqlite3.connect(path) as c:self.assertEqual('\n'.join(c.iterdump()),before);self.assertEqual(c.execute('PRAGMA user_version').fetchone()[0],2)
   new=WorkflowStore(path,self.child,schema_version=3);self.assertEqual(new.artifact('alpha',wid),artifact)
   with sqlite3.connect(path) as c:
    self.assertEqual(c.execute('PRAGMA user_version').fetchone()[0],3);self.assertEqual(c.execute('SELECT claim_owner,claim_epoch,lease_until FROM steps LIMIT 1').fetchone(),('',0,0.0));self.assertEqual(c.execute('SELECT count(*) FROM coordinator_audit').fetchone()[0],0)
   old.stop();new.stop()
 def test_24_fresh_schema3_failure_rolls_back_everything(self):
  with tempfile.TemporaryDirectory() as td:
   path=Path(td)/'fresh.sqlite'
   def fail(stage):
    if stage=='after_claim_owner':raise RuntimeError('fail')
   with self.assertRaises(RuntimeError):WorkflowStore(path,self.child,schema_version=3,migration_hook=fail)
   with sqlite3.connect(path) as c:self.assertEqual(c.execute('PRAGMA user_version').fetchone()[0],0);self.assertEqual(c.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall(),[])
 def test_25_stale_live_coordinator_cannot_settle_after_takeover(self):
  entered=threading.Event();release=threading.Event();clock=[1000.0];original=self.child.submit;first=[True]
  def held(*args):
   result=original(*args)
   if first[0]:first[0]=False;entered.set();release.wait(3)
   return result
  self.child.submit=held;a=self.store(coordinator_id='older',lease_seconds=.1,clock=lambda:clock[0]);b=self.store(coordinator_id='newer',lease_seconds=.1,clock=lambda:clock[0]);status,v=a.submit('alpha','stale',request());wid=v['workflow']['id'];errors=[]
  def tick():
   try:a.tick()
   except Exception as e:errors.append(type(e).__name__)
  t=threading.Thread(target=tick);t.start()
  try:
   self.assertTrue(entered.wait(2));b.tick();self.assertEqual(sum(x[0]=='submit' for x in self.child.calls),1);clock[0]+=1;b.tick();clock[0]+=1;b.tick();before=b.artifact('alpha',wid);self.assertEqual(before[0],200)
  finally:release.set();t.join(4)
  self.assertFalse(t.is_alive());self.assertFalse(errors);self.assertEqual(b.artifact('alpha',wid),before);self.assertEqual(sum(x[0]=='submit' for x in self.child.calls),2)
  rows=self.rows('coordinator_audit');self.assertTrue(any(x['event']=='stale_rejected' and x['coordinator_id']=='older' for x in rows));root=[x for x in self.rows('steps') if x['workflow_id']==wid and x['name']=='prepare'][0];self.assertGreaterEqual(root['claim_epoch'],2)
 def test_26_epoch_is_integer_and_increases_on_reacquisition(self):
  clock=[1000.0];a=self.store(coordinator_id='first',lease_seconds=.1,clock=lambda:clock[0]);b=self.store(coordinator_id='second',lease_seconds=.1,clock=lambda:clock[0]);self.child.offline=True;_,v=a.submit('alpha','epochs',request());wid=v['workflow']['id'];a.tick()
  with sqlite3.connect(self.path) as c:c.execute('UPDATE steps SET retry_at=0,lease_until=0 WHERE workflow_id=?',(wid,))
  b.tick();rows=[x for x in self.rows('coordinator_audit') if x['event']=='claim' and x['workflow_id']==wid];self.assertGreaterEqual(len(rows),2);epochs=[x['epoch'] for x in rows];self.assertTrue(all(type(x) is int and x>0 for x in epochs));self.assertEqual(epochs,sorted(set(epochs)))
 def test_27_schema3_global_quota_across_coordinators(self):
  stores=[self.store() for _ in range(12)];barrier=threading.Barrier(len(stores))
  def submit(i):barrier.wait();return stores[i].submit('alpha','ha-'+str(i),request())[0]
  with concurrent.futures.ThreadPoolExecutor(max_workers=12) as pool:states=list(pool.map(submit,range(12)))
  self.assertEqual(states.count(202),4);self.assertTrue(set(states)<={202,429,503});self.assertEqual(len(self.rows('workflows')),4)
 def test_28_pause_and_cancel_during_reserved_claim(self):
  clock=[1000.0];a=self.store(coordinator_id='first',clock=lambda:clock[0]);b=self.store(coordinator_id='second',clock=lambda:clock[0]);self.child.pending=True;_,v=a.submit('alpha','pause-cancel',request());wid=v['workflow']['id'];a.tick();a.pause('alpha',wid)
  for j in self.child.jobs.values():j['state']='completed'
  clock[0]+=4;b.tick();self.assertEqual(sum(x[0]=='submit' for x in self.child.calls),1);self.assertEqual(b.get('alpha',wid)[1]['workflow']['tasks'][1]['child_id'],None);a.cancel('alpha',wid);clock[0]+=4;b.tick();self.assertEqual(b.get('alpha',wid)[1]['workflow']['state'],'cancelled');self.assertEqual(b.artifact('alpha',wid)[0],409)
 def test_29_schema3_no_write_lock_during_network_io(self):
  entered=threading.Event();release=threading.Event();original=self.child.submit
  def held(*args):entered.set();release.wait(2);return original(*args)
  self.child.submit=held;self.submit();t=threading.Thread(target=self.q.tick);t.start()
  try:
   self.assertTrue(entered.wait(1));c=sqlite3.connect(self.path,timeout=.2);c.execute('BEGIN IMMEDIATE');c.rollback();c.close()
  finally:release.set();t.join(3)
  self.assertFalse(t.is_alive())
 def test_30_audit_mechanics_only_and_snapshot_hides_claim_internals(self):
  wid=self.submit(data=request('PRIVATE_DATA_CANARY'));self.pump(wid);rows=self.rows('coordinator_audit');self.assertTrue(rows);self.assertTrue(all(set(x)=={'id','coordinator_id','workflow_id','task','epoch','event'} for x in rows));self.assertTrue(all(x['event'] in ['claim','settle','stale_rejected'] for x in rows));self.assertNotIn('PRIVATE_DATA_CANARY',json.dumps(rows));snapshot=self.q.get('alpha',wid)[1]['workflow'];self.assertNotIn('claim_owner',json.dumps(snapshot));self.assertNotIn('lease_until',json.dumps(snapshot))
 def test_31_invalid_identity_and_lease_rejected(self):
  for kw in [{'coordinator_id':'bad id'},{'coordinator_id':'../../secret'},{'lease_seconds':True},{'lease_seconds':0},{'lease_seconds':float('inf')},{'lease_seconds':float('nan')}]:
   with self.subTest(keys=list(kw)),self.assertRaises(ValueError):self.store(**kw)
 def test_32_repeated_schema3_init_and_writer_lock(self):
  with sqlite3.connect(self.path) as c:before='\n'.join(c.iterdump())
  def fail(stage):raise RuntimeError('hook_should_not_repeat')
  q=self.store(migration_hook=fail)
  with sqlite3.connect(self.path) as c:self.assertEqual('\n'.join(c.iterdump()),before)
  c=sqlite3.connect(self.path);c.execute('BEGIN IMMEDIATE');began=time.monotonic()
  try:
   with self.assertRaises(sqlite3.OperationalError):self.store()
  finally:c.rollback();c.close()
  self.assertLess(time.monotonic()-began,.8);self.assertEqual(q.submit('alpha','after-lock',request())[0],202)


from recovery_engine import RecoveryManager

class Recovery(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.child=Child();self.q=WorkflowStore(self.root/'workflows.sqlite',self.child,schema_version=4);self.m=RecoveryManager(self.q,self.root)
 def tearDown(self):self.q.stop();self.tmp.cleanup()
 def admissions(self):
  with sqlite3.connect(self.root/'admissions.sqlite') as c:c.row_factory=sqlite3.Row;return [dict(x) for x in c.execute('SELECT * FROM admissions ORDER BY seq')]
 def manager(self,hook):return RecoveryManager(self.q,self.root,failpoint=hook)
 def finish(self,wid,owner='alpha'):
  for i in range(250):
   self.q.tick();v=self.q.get(owner,wid)[1]['workflow']
   if v['state']=='completed':return v
   time.sleep(.025)
  self.fail('live_workflow_not_complete')
 def snapshot(self,label='before'):
  status,v=self.m.snapshot('alpha',label);self.assertEqual(status,201,v);return v['snapshot']
 def restore(self,snapshot='before',clone='clone'):
  status,v=self.m.restore('alpha',snapshot,clone);self.assertEqual(status,201,v);self.assertIs(v['clone']['active'],False);return self.root/'restores'/clone/'workflows.sqlite'
 def test_33_schema4_is_additive_and_repeatable(self):
  with sqlite3.connect(self.q.path) as c:self.assertEqual(c.execute('PRAGMA user_version').fetchone()[0],4);self.assertTrue(any(x[1]=='retention_class' and x[4]=="'standard'" for x in c.execute('PRAGMA table_info(workflows)')))
  def hook(_):raise RuntimeError('must_not_repeat')
  for requested in [1,2,3,4]:WorkflowStore(self.q.path,self.child,schema_version=requested,migration_hook=hook)
 def test_34_v3_and_fresh_migration_hook_failure_rolls_back(self):
  for existing in [False,True]:
   p=self.root/('old.sqlite' if existing else 'fresh.sqlite')
   if existing:WorkflowStore(p,self.child,schema_version=3)
   before=p.read_bytes() if existing else None
   def hook(stage):
    if stage=='after_retention_class':raise RuntimeError('PRIVATE_ERROR_CANARY')
   with self.assertRaisesRegex(RuntimeError,'PRIVATE_ERROR_CANARY'):WorkflowStore(p,self.child,schema_version=4,migration_hook=hook)
   with sqlite3.connect(p) as c:
    self.assertEqual(c.execute('PRAGMA user_version').fetchone()[0],3 if existing else 0);self.assertFalse(any(x[1]=='retention_class' for x in c.execute('PRAGMA table_info(workflows)')))
    if not existing:self.assertEqual(c.execute("SELECT count(*) FROM sqlite_master WHERE type='table'").fetchone()[0],0)
 def test_35_reject_future_and_invalid_versions_without_mutation(self):
  for v in [True,0,-1,5,999,'4']:
   with self.subTest(v=v),self.assertRaises(ValueError):WorkflowStore(self.q.path,self.child,schema_version=v)
  p=self.root/'future.sqlite'
  with sqlite3.connect(p) as c:c.execute('PRAGMA user_version=999');c.execute('CREATE TABLE unchanged(value INTEGER)');c.execute('INSERT INTO unchanged VALUES(1)')
  before=p.read_bytes()
  with self.assertRaises(ValueError):WorkflowStore(p,self.child,schema_version=4)
  self.assertEqual(p.read_bytes(),before)
 def test_36_reserved_identity_replay_and_collision_are_atomic(self):
  wid='a'*32;s,v=self.q.submit('alpha','one',request(),reserved_id=wid);self.assertEqual(s,202);self.assertEqual(v['workflow']['id'],wid)
  self.assertEqual(self.q.submit('alpha','one',request(),reserved_id='b'*32)[0],409);self.assertEqual(self.q.submit('beta','other',request(),reserved_id=wid)[0],409)
  for bad in [True,False,0,'','bad','../x',123]:self.assertEqual(self.q.submit('alpha','bad',request(),reserved_id=bad)[0],422)
  with sqlite3.connect(self.q.path) as c:self.assertEqual(c.execute('SELECT count(*) FROM workflows').fetchone()[0],1)
 def test_37_intent_survives_failure_before_workflow_and_reuses_id(self):
  def hook(stage):
   if stage=='after_intent':raise RuntimeError('PRIVATE_ERROR_CANARY')
  with self.assertRaises(RuntimeError):self.manager(hook).submit('alpha','intent',request())
  rows=self.admissions();self.assertEqual(len(rows),1);self.assertEqual(rows[0]['state'],'intent');wid=rows[0]['workflow_id'];self.assertEqual(self.q.lookup_request('alpha','intent')[0],404)
  s,v=self.m.submit('alpha','intent',request());self.assertEqual(s,202);self.assertEqual(v['workflow']['id'],wid);self.assertEqual(self.admissions()[0]['state'],'accepted')
 def test_38_workflow_commit_before_journal_ack_reconciles_same_id(self):
  def hook(stage):
   if stage=='after_workflow_commit':raise RuntimeError('PRIVATE_ERROR_CANARY')
  with self.assertRaises(RuntimeError):self.manager(hook).submit('alpha','ack',request())
  old=self.q.lookup_request('alpha','ack')[1]['workflow']['id'];self.assertEqual(self.admissions()[0]['state'],'intent');s,v=self.m.submit('alpha','ack',request());self.assertEqual(s,200);self.assertEqual(v['workflow']['id'],old);self.assertEqual(self.admissions()[0]['state'],'accepted')
 def test_39_validation_conflicts_and_no_journal_lock_over_workflow(self):
  self.assertEqual(self.m.submit('alpha','bad',request(extra=True))[0],422);self.assertEqual(self.admissions(),[])
  original=self.q.submit
  def probe(*args,**kw):
   with sqlite3.connect(self.root/'admissions.sqlite',timeout=.1) as c:c.execute('BEGIN IMMEDIATE');c.rollback()
   return original(*args,**kw)
  self.q.submit=probe;s,v=self.m.submit('alpha','one',request());self.assertEqual(s,202);self.assertEqual(self.m.submit('alpha','one',request('different'))[0],409);self.assertEqual(len(self.admissions()),1)
 def test_40_concurrent_same_key_has_one_durable_identity(self):
  managers=[RecoveryManager(WorkflowStore(self.q.path,self.child,schema_version=4),self.root) for _ in range(8)];barrier=threading.Barrier(8)
  def submit(i):barrier.wait();return managers[i].submit('alpha','same',request())
  with concurrent.futures.ThreadPoolExecutor(max_workers=8) as p:results=list(p.map(submit,range(8)))
  self.assertTrue(all(s in [200,202,503] for s,v in results));self.assertTrue(any(s in [200,202] for s,v in results));s,v=self.m.submit('alpha','same',request());self.assertEqual(s,200);self.assertEqual(len(self.admissions()),1);self.assertEqual(self.admissions()[0]['workflow_id'],v['workflow']['id'])
 def test_41_health_detects_real_journal_writer_and_retry(self):
  c=sqlite3.connect(self.root/'admissions.sqlite',isolation_level=None);c.execute('BEGIN IMMEDIATE')
  try:
   self.assertEqual(self.m.health()[0],503);s,v=self.m.submit('alpha','locked',request());self.assertEqual(s,503);self.assertNotIn('database is locked',json.dumps(v).lower());self.assertEqual(self.q.lookup_request('alpha','locked')[0],404)
  finally:c.rollback();c.close()
  self.assertEqual(self.m.health()[0],200);self.assertEqual(self.m.submit('alpha','locked',request())[0],202)
 def test_42_online_backup_includes_committed_wal_not_uncommitted_writer(self):
  c=sqlite3.connect(self.q.path,isolation_level=None);c.execute('CREATE TABLE online_backup_probe(id INTEGER PRIMARY KEY,value INTEGER)');c.execute('INSERT INTO online_backup_probe VALUES(1,1)');c.execute('BEGIN IMMEDIATE');c.execute('UPDATE online_backup_probe SET value=2 WHERE id=1')
  try:
   manifest=self.snapshot();p=self.root/'snapshots'/manifest['database_file']
   with sqlite3.connect(p) as q:self.assertEqual(q.execute('SELECT value FROM online_backup_probe').fetchone()[0],1);self.assertEqual(q.execute('PRAGMA integrity_check').fetchone()[0],'ok')
   self.assertEqual(hashlib.sha256(p.read_bytes()).hexdigest(),manifest['database_sha256']);self.assertEqual(len(p.read_bytes()),manifest['database_bytes'])
  finally:c.commit();c.close()
  with sqlite3.connect(self.q.path) as c:self.assertEqual(c.execute('SELECT value FROM online_backup_probe').fetchone()[0],2)
 def test_43_snapshot_publication_no_replace_and_failure_cleanup(self):
  manifest=self.snapshot();original=(self.root/'snapshots'/'before.json').read_bytes();self.assertEqual(self.m.snapshot('alpha','before')[0],409);self.assertEqual((self.root/'snapshots'/'before.json').read_bytes(),original)
  def hook(stage):
   if stage=='before_snapshot_publish':raise RuntimeError('PRIVATE_ERROR_CANARY')
  with self.assertRaises(RuntimeError):self.manager(hook).snapshot('alpha','failed')
  self.assertFalse((self.root/'snapshots'/'failed.json').exists());self.assertEqual(self.m.restore('alpha','failed','absent')[0],404)
 def test_44_concurrent_snapshot_label_has_one_winner(self):
  barrier=threading.Barrier(2)
  def create(i):m=RecoveryManager(WorkflowStore(self.q.path,self.child,schema_version=4),self.root);barrier.wait();return m.snapshot('alpha','concurrent')[0]
  with concurrent.futures.ThreadPoolExecutor(max_workers=2) as p:codes=list(p.map(create,range(2)))
  self.assertEqual(sorted(codes),[201,409]);self.restore('concurrent','concurrent-clone')
 def test_45_restore_inactive_no_replace_and_foreign_denied(self):
  self.snapshot();before=Path(self.q.path).read_bytes();clone=self.restore();self.assertNotEqual(clone.resolve(),Path(self.q.path).resolve());self.assertEqual(self.m.restore('alpha','before','clone')[0],409);self.assertEqual(self.m.restore('beta','before','foreign')[0],404);self.assertEqual(Path(self.q.path).read_bytes(),before);self.assertEqual(self.child.calls,[])
 def test_46_corruption_paths_and_symlink_fail_without_writes(self):
  m=self.snapshot();sn=self.root/'snapshots';original=(sn/'before.json').read_bytes();sentinel=self.root/'sentinel';sentinel.write_bytes(b'PRIVATE_ERROR_CANARY');before=sentinel.read_bytes()
  for name,value in [('hash',{**m,'database_sha256':'0'*64}),('path',{**m,'database_file':'../sentinel'}),('schema',{**m,'schema':999}),('link',{**m,'database_file':'linked.sqlite'})]:
   if name=='link':(sn/'linked.sqlite').symlink_to(sn/m['database_file'])
   (sn/'before.json').write_text(json.dumps(value));s,v=self.m.restore('alpha','before','blocked-'+name);self.assertEqual(s,409);self.assertFalse((self.root/'restores'/('blocked-'+name)/'workflows.sqlite').exists())
  (sn/'before.json').write_bytes(original)
  for bad in ['../escape','/tmp/escape','a/b','A','a'*33]:self.assertEqual(self.m.restore('alpha','before',bad)[0],422)
  self.assertEqual(sentinel.read_bytes(),before)
 def test_47_new_control_blocks_stale_restore_and_foreign_control_is_not_logged(self):
  _,v=self.m.submit('alpha','one',request());wid=v['workflow']['id'];self.snapshot();self.assertEqual(self.m.control('beta',wid,'cancel')[0],404)
  with sqlite3.connect(self.root/'admissions.sqlite') as c:self.assertEqual(c.execute('SELECT count(*) FROM controls').fetchone()[0],0)
  self.assertEqual(self.m.control('alpha',wid,'pause')[0],200);s,v=self.m.restore('alpha','before','blocked');self.assertEqual((s,v.get('error')),(409,'control_reconciliation_required'));self.assertEqual(self.q.get('alpha',wid)[1]['workflow']['paused'],True)
 def test_48_control_after_restore_blocks_reconciliation(self):
  _,v=self.m.submit('alpha','one',request());wid=v['workflow']['id'];self.snapshot();self.restore();self.m.control('alpha',wid,'cancel');s,v=self.m.reconcile('alpha','clone');self.assertEqual((s,v.get('error')),(409,'control_reconciliation_required'));self.assertEqual(self.child.calls,[])
 def test_49_stale_clone_adopts_committed_children_without_post_or_effect_replay(self):
  self.snapshot();ids={}
  for owner in ['alpha','beta']:
   s,v=self.m.submit(owner,'after-'+owner,request(owner));self.assertEqual(s,202);ids[owner]=v['workflow']['id'];self.finish(ids[owner],owner)
  before_calls=len([x for x in self.child.calls if x[0]=='submit']);oldjobs=copy.deepcopy(self.child.jobs);clone=self.restore();s,v=self.m.reconcile('alpha','clone');self.assertEqual(s,200,v);self.assertIs(v['complete'],True);self.assertIs(v['active'],False);self.assertEqual(v['unresolved'],0)
  self.assertEqual(len([x for x in self.child.calls if x[0]=='submit']),before_calls);self.assertEqual(self.child.jobs,oldjobs)
  q=WorkflowStore(clone,self.child,schema_version=4)
  try:
   for owner,wid in ids.items():self.assertEqual(q.get(owner,wid),self.q.get(owner,wid));self.assertEqual(q.artifact(owner,wid),self.q.artifact(owner,wid))
  finally:q.stop()
 def test_50_intent_unknown_is_reported_not_activated_or_marked_complete(self):
  def hook(stage):
   if stage=='after_intent':raise RuntimeError('PRIVATE_ERROR_CANARY')
  with self.assertRaises(RuntimeError):self.manager(hook).submit('alpha','unknown',request())
  self.snapshot();self.restore();s,v=self.m.reconcile('alpha','clone');self.assertEqual(s,202);self.assertIs(v['complete'],False);self.assertIs(v['active'],False);self.assertGreaterEqual(v['unresolved'],1);self.assertEqual(self.child.calls,[])
 def test_51_tampered_journal_hash_cannot_dispatch(self):
  self.snapshot();_,v=self.m.submit('alpha','after',request());self.finish(v['workflow']['id']);self.restore();before=copy.deepcopy(self.child.calls)
  with sqlite3.connect(self.root/'admissions.sqlite') as c:c.execute("UPDATE admissions SET payload_sha256=?",('0'*64,))
  s,v=self.m.reconcile('alpha','clone');self.assertEqual(s,409);self.assertIsNot(v.get('complete'),True);self.assertEqual(self.child.calls,before)
 def test_52_mechanical_audit_excludes_payload_and_raw_error(self):
  self.m.submit('alpha','one',request('PRIVATE_DATA_CANARY'));self.m.snapshot('alpha','one')
  with sqlite3.connect(self.root/'recovery_audit.sqlite') as c:
   c.row_factory=sqlite3.Row;rows=[dict(x) for x in c.execute('SELECT * FROM events')]
  self.assertTrue(rows);self.assertTrue(all(set(x)=={'id','event','owner_sha256','key_sha256','error_type'} for x in rows));raw=json.dumps(rows);self.assertNotIn('PRIVATE_DATA_CANARY',raw);self.assertNotIn('PRIVATE_ERROR_CANARY',raw);self.assertNotIn('alpha',raw)

 def test_53_pending_control_below_snapshot_watermark_is_not_safe(self):
  _,v=self.m.submit('alpha','one',request());wid=v['workflow']['id']
  with sqlite3.connect(self.root/'admissions.sqlite') as c:c.execute("INSERT INTO controls(owner,workflow_id,action,state) VALUES(?,?,?,?)",('alpha',wid,'pause','intent'))
  status,value=self.m.snapshot('alpha','unsafe');self.assertEqual((status,value.get('error')),(409,'control_reconciliation_required'));self.assertFalse((self.root/'snapshots'/'unsafe.json').exists())
 def test_54_tick_selection_bound_preserves_legacy_default(self):
  import inspect
  self.assertIn('max_steps',inspect.signature(self.q.tick).parameters)
  for bad in [True,0,-1,49,'1']:
   with self.subTest(bad=bad),self.assertRaises(ValueError):self.q.tick(max_steps=bad)
  self.m.submit('alpha','first',request());self.m.submit('beta','second',request());self.q.tick(max_steps=1);self.assertEqual(sum(x[0]=='submit' for x in self.child.calls),1)

 def test_55_quota_rejection_is_not_unknown_and_retry_retains_id(self):
  ids=[]
  for i in range(4):
   status,value=self.m.submit('alpha','cap-'+str(i),request());self.assertEqual(status,202);ids.append(value['workflow']['id'])
  self.assertEqual(self.m.submit('alpha','quota',request())[0],429);row=next(x for x in self.admissions() if x['key']=='quota');self.assertEqual(row['state'],'rejected');reserved=row['workflow_id'];self.assertEqual(self.q.lookup_request('alpha','quota')[0],404)
  self.assertEqual(self.m.control('alpha',ids[0],'cancel')[0],200);status,value=self.m.submit('alpha','quota',request());self.assertEqual(status,202);self.assertEqual(value['workflow']['id'],reserved);self.assertEqual(next(x for x in self.admissions() if x['key']=='quota')['state'],'accepted')

 def test_56_new_rpc_store_can_backup_existing_v4_while_writer_is_open(self):
  with sqlite3.connect(self.q.path) as c:
   c.execute('CREATE TABLE online_backup_probe(id INTEGER PRIMARY KEY,value INTEGER)');c.execute('INSERT INTO online_backup_probe VALUES(1,0)')
  writer=sqlite3.connect(self.q.path);writer.execute('BEGIN IMMEDIATE');writer.execute('UPDATE online_backup_probe SET value=1 WHERE id=1')
  try:
   reopened=WorkflowStore(self.q.path,self.child,schema_version=4)
   manager=RecoveryManager(reopened,self.root);status,value=manager.snapshot('alpha','rpcwriter');self.assertEqual(status,201,value);self.assertTrue(writer.in_transaction)
   with sqlite3.connect(self.root/'snapshots'/'rpcwriter.sqlite') as snap:self.assertEqual(snap.execute('SELECT value FROM online_backup_probe').fetchone()[0],0)
  finally:writer.rollback();writer.close()

if __name__=='__main__':unittest.main(verbosity=2)
