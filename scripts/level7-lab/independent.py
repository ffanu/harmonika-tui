"""Held-out behavioral tests; same suite runs locally and in deployed image."""
import base64,hashlib,json,os,sqlite3,stat,sys,tempfile,threading,time,unittest
from pathlib import Path
ROOT=Path(os.environ['HARMONIKA_EXAM_PROJECT']);sys.path.insert(0,str(ROOT));PHASE=os.environ['HARMONIKA_EXAM_PHASE']
from engine import Queue
from effect_connector import OutcomeUnknown,StaleFence
class Sink:
 def __init__(self):self.lock=threading.RLock();self.fences={};self.effects={};self.calls=[];self.gates={};self.entered=threading.Event();self.offline=False;self.corrupt=False
 def authorize(self,id,owner,fence):
  self.calls.append(('authorize',id,fence))
  if self.offline:raise OutcomeUnknown('PRIVATE_ERROR_CANARY')
  with self.lock:
   old=self.fences.get(id)
   if old and (old[0]!=owner or old[1]>fence):raise StaleFence()
   self.fences[id]=(owner,fence)
  return {'authorized':True,'fence':fence}
 def lookup(self,id):
  self.calls.append(('lookup',id))
  if self.offline:raise OutcomeUnknown('PRIVATE_ERROR_CANARY')
  return self.effects.get(id)
 def execute(self,id,payload,fence):
  self.calls.append(('execute',id,fence));self.entered.set()
  if fence in self.gates:self.gates[fence].wait(4)
  with self.lock:
   owner,current=self.fences[id]
   if current!=fence:raise StaleFence()
   raw=json.dumps(payload,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode();value={'job_id':id,'owner':owner,'fence':fence,'sha256':hashlib.sha256(raw).hexdigest(),'content_b64':base64.b64encode(raw).decode()};self.effects.setdefault(id,value)
   if self.corrupt:self.corrupt=False;return {**self.effects[id],'job_id':'foreign','owner':'beta'}
   return self.effects[id]
def payload(s='fixture α'):return {'kind':'report','text':s}
class Core(unittest.TestCase):
 def setUp(self):self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)/'jobs.sqlite';self.s=Sink();self.queues=[];self.q=self.new('worker1')
 def new(self,worker,lease=.4):
  q=Queue(str(self.path),self.s,worker_id=worker,lease_seconds=lease);self.queues.append(q);return q
 def tearDown(self):
  for gate in self.s.gates.values():gate.set()
  for q in self.queues:q.stop()
  self.tmp.cleanup()
 def create(self,key='one',owner='alpha',data=None):
  status,v=self.q.submit(owner,key,data or payload());self.assertEqual(status,202);return v['job']['id']
 def wait(self,id,state='completed',owner='alpha',seconds=4):
  until=time.monotonic()+seconds
  while time.monotonic()<until:
   status,v=self.q.get(owner,id)
   if status==200 and v['job']['state']==state:return v['job']
   time.sleep(.025)
  self.fail('required_job_state_missing')
 def test_01_global_admission_and_replay(self):
  ids=[self.create(str(i)) for i in range(8)];self.assertEqual(self.q.submit('alpha','extra',payload())[0],429);self.assertEqual(self.q.submit('alpha','0',payload())[0],200);self.assertEqual(self.q.submit('alpha','0',payload('changed'))[0],409);self.create('0','beta');self.assertEqual(self.q.lookup_request('beta','extra')[0],404);self.assertEqual(self.q.lookup_request('alpha','0')[1]['job']['id'],ids[0])
 def test_02_input_atomic_and_scope(self):
  for value in [None,True,[],{}, {'kind':'report','text':True},{'kind':'report','text':' '},{'kind':'report','text':'x'*257},{'kind':'report','text':'x','owner':'beta'}]:self.assertEqual(self.q.submit('alpha','bad',value)[0],422)
  self.assertEqual(self.q.events('alpha',0)[1]['events'],[]);id=self.create();self.assertEqual(self.q.get('beta',id)[0],404);self.assertEqual(self.q.cancel('beta',id)[0],404);self.assertEqual(self.q.artifact('beta',id)[0],404)
 def test_03_parallel_admission_single_slot(self):
  for i in range(7):self.create(str(i))
  barrier=threading.Barrier(3);out=[]
  def work(i):barrier.wait();out.append(self.q.submit('alpha','parallel-'+str(i),payload()))
  threads=[threading.Thread(target=work,args=(i,)) for i in range(3)]
  for t in threads:t.start()
  for t in threads:t.join(3)
  self.assertEqual(sum(s==202 for s,v in out),1);self.assertEqual(sum(s==429 for s,v in out),2)
 def test_04_three_workers_claim_distinct_jobs(self):
  gate=threading.Event();self.s.gates[1]=gate;ids=[self.create(str(i)) for i in range(3)];self.q.start();b=self.new('worker2');c=self.new('worker3');b.start();c.start();until=time.monotonic()+2
  while time.monotonic()<until:
   rows=[self.q.get('alpha',id)[1]['job'] for id in ids]
   if {j.get('worker_id') for j in rows}=={'worker1','worker2','worker3'}:break
   time.sleep(.025)
  self.assertEqual({j['worker_id'] for j in rows},{'worker1','worker2','worker3'});self.assertTrue(all(j['fence']>=1 for j in rows));gate.set()
  for id in ids:self.wait(id)
  self.assertEqual(len(self.s.effects),3)
 def test_05_late_old_worker_cannot_finalize_new_claim(self):
  gate=threading.Event();self.s.gates[1]=gate;id=self.create();self.q.start();self.assertTrue(self.s.entered.wait(2))
  with sqlite3.connect(self.path) as c:c.execute('UPDATE jobs SET worker_id=?,fence=?,lease_until=? WHERE id=?',('worker2',2,time.time()+3,id))
  gate.set();time.sleep(.15);job=self.q.get('alpha',id)[1]['job'];self.assertEqual(job['worker_id'],'worker2');self.assertEqual(job['fence'],2);self.assertNotEqual(job['state'],'completed');self.q.stop()
  with sqlite3.connect(self.path) as c:c.execute('UPDATE jobs SET lease_until=0 WHERE id=?',(id,))
  b=self.new('worker2');b.start();job=self.wait(id);self.assertEqual(job['worker_id'],'worker2');self.assertGreaterEqual(job['fence'],3);self.assertEqual(sum(x[0]=='execute' for x in self.s.calls),1)
 def test_06_receipt_identity_requires_reconciliation(self):
  self.s.corrupt=True;id=self.create();self.q.start();self.wait(id);self.assertIn(('lookup',id),self.s.calls);self.assertEqual(sum(x[0]=='execute' for x in self.s.calls),1);self.assertEqual(self.q.artifact('alpha',id)[1],json.dumps(payload(),sort_keys=True,separators=(',',':'),ensure_ascii=False).encode())
 def test_07_offline_stays_unknown_and_bounded(self):
  self.s.offline=True;id=self.create();self.q.start();time.sleep(.25);self.assertNotIn(self.q.get('alpha',id)[1]['job']['state'],['completed','cancelled']);self.assertEqual(self.q.artifact('alpha',id)[0],409);self.assertLessEqual(len(self.s.calls),8);self.s.offline=False;self.wait(id)
 def test_08_lock_atomic_wal_and_drain(self):
  c=sqlite3.connect(self.path);self.assertEqual(c.execute('PRAGMA journal_mode').fetchone()[0],'wal');c.execute('BEGIN IMMEDIATE');started=time.monotonic()
  try:self.assertEqual(self.q.submit('alpha','locked',payload())[0],503);self.assertLess(time.monotonic()-started,1)
  finally:c.rollback();c.close()
  self.assertEqual(self.q.events('alpha',0)[1]['events'],[]);self.create();self.assertEqual(self.q.drain(),(200,{'draining':True}));self.assertEqual(self.q.submit('alpha','fresh',payload())[0],503);self.assertEqual(self.q.submit('alpha','one',payload())[0],200);b=self.new('worker2');self.assertEqual(b.submit('alpha','fresh',payload())[0],503);self.assertEqual(b.resume(),(200,{'draining':False}))
 def test_09_events_cursor_and_no_payload(self):
  id=self.create(data=payload('PRIVATE_DATA_CANARY'));self.q.start();self.wait(id);es=self.q.events('alpha',0)[1]['events'];self.assertTrue(es);self.assertEqual([e['id'] for e in es],sorted({e['id'] for e in es}));self.assertTrue(all(set(e)=={'id','job_id','state'} for e in es));self.assertNotIn('PRIVATE_DATA_CANARY',json.dumps(es));self.assertEqual(self.q.events('alpha',es[-1]['id'])[1]['events'],[]);self.assertEqual(self.q.events('beta',0)[1]['events'],[])
 def test_10_additive_level6_migration(self):
  other=Path(self.tmp.name)/'legacy.sqlite';raw=b'{"kind":"report","text":"legacy"}'
  with sqlite3.connect(other) as c:
   c.executescript('CREATE TABLE jobs(id TEXT PRIMARY KEY,owner TEXT,key TEXT,payload TEXT,state TEXT,attempts INTEGER DEFAULT 0,artifact BLOB,sha256 TEXT,UNIQUE(owner,key)); CREATE TABLE events(id INTEGER PRIMARY KEY AUTOINCREMENT,job_id TEXT,owner TEXT,state TEXT);CREATE TABLE admission(id INTEGER PRIMARY KEY,draining INTEGER);INSERT INTO admission VALUES(1,0);');c.execute('INSERT INTO jobs VALUES(?,?,?,?,?,?,?,?)',('legacy','alpha','old',raw.decode(),'completed',1,raw,hashlib.sha256(raw).hexdigest()));c.execute("INSERT INTO events(job_id,owner,state) VALUES('legacy','alpha','completed')")
  q=Queue(str(other),self.s,worker_id='worker3');self.queues.append(q);self.assertEqual(q.submit('alpha','old',json.loads(raw))[0],200);self.assertEqual(q.artifact('alpha','legacy'),(200,raw));self.assertEqual(len(q.events('alpha',0)[1]['events']),1)
@unittest.skipUnless(PHASE=='v2','turn_b_only')
class Stream(unittest.TestCase):
 def reader(self,**kw):
  from stream_reader import EventReader
  return EventReader(**kw)
 def frame(self,id=1,job='j',state='completed',typ='job'):
  return ('id: '+str(id)+'\r\nevent: '+typ+'\r\ndata: '+json.dumps({'id':id,'job_id':job,'state':state},ensure_ascii=False)+'\r\n\r\n').encode()
 def test_11_every_byte_utf8_crlf(self):
  r=self.reader();wire=': siap ✓\r\n\r\n'.encode()+self.frame(job='uji-α');out=[]
  for b in wire:out.extend(r.feed(bytes([b])))
  self.assertEqual(out,[{'id':1,'job_id':'uji-α','state':'completed'}]);self.assertEqual(r.last_event_id,1);r.finish()
 def test_12_partial_not_delivered(self):
  r=self.reader();f=self.frame();self.assertEqual(r.feed(f[:-2]),[]);self.assertEqual(r.last_event_id,0);self.assertEqual(len(r.feed(f[-2:])),1)
 def test_13_duplicate_numeric_foreign(self):
  r=self.reader(allowed_job_ids={'j'},last_event_id=8);out=r.feed(self.frame(9)+self.frame(10)+self.frame(9)+self.frame(11,'foreign'));self.assertEqual([e['id'] for e in out],[9,10]);self.assertEqual(r.last_event_id,10)
 def test_14_malformed_no_cursor_advance(self):
  for f in [b'id: 1\ndata: nope\n\n',b'id: 2\ndata: {"id":1,"job_id":"j","state":"completed"}\n\n',b'id: 1\ndata: {"id":true,"job_id":"j","state":"completed"}\n\n',self.frame(state='garbage'),b'id: -1\ndata: {"id":-1,"job_id":"j","state":"completed"}\n\n',b'data: {"id":1,"job_id":"j","state":"completed"}\n\n']:
   r=self.reader()
   with self.assertRaises(ValueError):r.feed(f)
   self.assertEqual(r.last_event_id,0)
 def test_15_multiline_unknown_type(self):
  r=self.reader();wire=b'id: 1\nevent: job\ndata: {"id":1,\ndata: "job_id":"j","state":"queued"}\n\n';self.assertEqual(r.feed(wire),[{'id':1,'job_id':'j','state':'queued'}]);self.assertEqual(r.feed(self.frame(2,typ='log')),[]);self.assertEqual(r.last_event_id,1)
  self.assertEqual(r.feed(b'id: 2\ndata: {"id":2,"job_id":"j","state":"completed"}\n\n'),[{'id':2,'job_id':'j','state':'completed'}])
  self.assertEqual(r.feed(b'id: 3\nevent: message\ndata: {"id":3,"job_id":"j","state":"queued"}\n\n'),[{'id':3,'job_id':'j','state':'queued'}]);self.assertEqual(r.last_event_id,3)
 def test_16_overflow_comments_and_finish_partial(self):
  r=self.reader(max_frame_bytes=64)
  with self.assertRaises(ValueError):r.feed(b':'+b'x'*65)
  self.assertEqual(r.last_event_id,0)
  for raw in [b'id: 1\ndata: ',b': hb\r',b'\xe2\x82']:
   r=self.reader();r.feed(raw)
   with self.assertRaises(ValueError):r.finish()
   self.assertEqual(r.last_event_id,0)
@unittest.skipUnless(PHASE=='v2','turn_b_only')
class Storage(unittest.TestCase):
 def setUp(self):
  import storage_tools
  self.tools=storage_tools;self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.source=self.root/'source.sqlite';self.c=sqlite3.connect(self.source);self.c.execute('PRAGMA journal_mode=WAL');self.c.executescript('CREATE TABLE jobs(id TEXT PRIMARY KEY);CREATE TABLE events(id INTEGER PRIMARY KEY);CREATE TABLE admission(id INTEGER PRIMARY KEY,draining INTEGER);INSERT INTO admission VALUES(1,0);INSERT INTO jobs VALUES("old");INSERT INTO events VALUES(1);');self.c.commit()
 def tearDown(self):self.c.close();self.tmp.cleanup()
 def test_17_online_snapshot_manifest(self):
  reader=sqlite3.connect(self.source);reader.execute('BEGIN');reader.execute('SELECT * FROM jobs').fetchall();self.c.execute('INSERT INTO jobs VALUES("new")');self.c.execute('INSERT INTO events VALUES(2)');self.c.commit();self.c.execute('BEGIN IMMEDIATE');self.c.execute('UPDATE admission SET draining=draining');dest=self.root/'snapshot.sqlite'
  try:m=self.tools.backup(str(self.source),str(dest));self.assertTrue(self.c.in_transaction)
  finally:self.c.rollback();reader.rollback();reader.close()
  self.assertEqual(m['sha256'],hashlib.sha256(dest.read_bytes()).hexdigest());self.assertEqual(m['bytes'],dest.stat().st_size);self.assertEqual((m['job_count'],m['event_count'],m['last_event_id']),(2,2,2));self.assertEqual(stat.S_IMODE(dest.stat().st_mode),0o600)
  with sqlite3.connect(dest) as c:self.assertEqual(c.execute('PRAGMA journal_mode').fetchone()[0],'delete');self.assertEqual(c.execute('SELECT count(*) FROM jobs').fetchone()[0],2)
  self.assertEqual(self.c.execute('PRAGMA journal_mode').fetchone()[0],'wal')
 def snapshot(self):
  p=self.root/'snapshot.sqlite';m=self.tools.backup(str(self.source),str(p));return p,m
 def test_18_restore_exact_and_existing(self):
  p,m=self.snapshot();dest=self.root/'clone.sqlite';self.tools.restore(str(p),str(dest),m['sha256']);self.assertEqual(dest.read_bytes(),p.read_bytes());self.assertEqual(stat.S_IMODE(dest.stat().st_mode),0o600)
  with self.assertRaises((ValueError,OSError,sqlite3.DatabaseError)):self.tools.restore(str(p),str(dest),m['sha256'])
  self.assertEqual(dest.read_bytes(),p.read_bytes())
 def test_19_wrong_hash_and_corrupt_no_publish(self):
  p,m=self.snapshot();dest=self.root/'bad.sqlite'
  with self.assertRaises((ValueError,OSError,sqlite3.DatabaseError)):self.tools.restore(str(p),str(dest),'0'*64)
  self.assertFalse(dest.exists());p.write_bytes(b'not sqlite')
  with self.assertRaises((ValueError,OSError,sqlite3.DatabaseError)):self.tools.restore(str(p),str(dest),hashlib.sha256(p.read_bytes()).hexdigest())
  self.assertFalse(dest.exists())
 def test_20_symlink_source_and_destination(self):
  p,m=self.snapshot();link=self.root/'source-link';link.symlink_to(p);dest=self.root/'clone.sqlite'
  with self.assertRaises((ValueError,OSError,sqlite3.DatabaseError)):self.tools.restore(str(link),str(dest),m['sha256'])
  self.assertFalse(dest.exists());dest.symlink_to(self.root/'missing.sqlite')
  with self.assertRaises((ValueError,OSError,sqlite3.DatabaseError)):self.tools.restore(str(p),str(dest),m['sha256'])
  self.assertTrue(dest.is_symlink());self.assertFalse((self.root/'missing.sqlite').exists())
 def test_21_competing_publish_one_winner(self):
  self.c.close();self.c=sqlite3.connect(self.source);target=self.root/'snapshot.sqlite';barrier=threading.Barrier(6);out=[]
  def one():
   barrier.wait()
   try:self.tools.backup(str(self.source),str(target));out.append('success')
   except (OSError,ValueError,sqlite3.DatabaseError):out.append('rejected')
  ts=[threading.Thread(target=one) for _ in range(6)]
  for t in ts:t.start()
  for t in ts:t.join(5)
  self.assertEqual(out.count('success'),1);self.assertEqual(out.count('rejected'),5);self.assertEqual(stat.S_IMODE(target.stat().st_mode),0o600)
if __name__=='__main__':unittest.main(verbosity=2)
