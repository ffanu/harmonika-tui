"""Independent local tests of candidate queue state, transaction and recovery semantics."""
import base64,hashlib,importlib.util,json,os,sqlite3,tempfile,threading,time,unittest
from pathlib import Path
ROOT=Path(os.environ['HARMONIKA_EXAM_PROJECT'])
import sys
sys.path.insert(0,str(ROOT))
from engine import Queue
from effect_connector import OutcomeUnknown
class Sink:
 def __init__(self):self.receipts={};self.calls=[];self.hold=threading.Event();self.hold.set();self.entered=threading.Event();self.unknown=False;self.offline=False
 def execute(self,id,payload):
  self.calls.append(('execute',id));self.entered.set();self.hold.wait(5)
  raw=json.dumps(payload,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode();self.receipts[id]={'sha256':hashlib.sha256(raw).hexdigest(),'content_b64':base64.b64encode(raw).decode()}
  if self.unknown:self.unknown=False;raise OutcomeUnknown('sensitive raw value MUST NOT be logged')
  return self.receipts[id]
 def lookup(self,id):
  self.calls.append(('lookup',id))
  if self.offline:raise OutcomeUnknown('private downstream failure')
  return self.receipts.get(id)
def payload(text='fixture α'):return {'kind':'report','text':text}
class Acceptance(unittest.TestCase):
 def setUp(self):self.tmp=tempfile.TemporaryDirectory();self.s=Sink();self.q=Queue(str(Path(self.tmp.name)/'jobs.sqlite'),self.s)
 def tearDown(self):self.s.hold.set();self.q.stop();self.tmp.cleanup()
 def wait(self,id,state='completed'):
  for _ in range(120):
   value=self.q.get('alpha',id)[1]['job']
   if value['state']==state:return value
   time.sleep(.025)
  self.fail('state_not_reached:'+state)
 def create(self,key='one',owner='alpha',p=None):
  status,v=self.q.submit(owner,key,p or payload());self.assertEqual(status,202);return v['job']['id']
 def test_01_schema_wal(self):
  with sqlite3.connect(str(Path(self.tmp.name)/'jobs.sqlite')) as c:self.assertEqual(c.execute('PRAGMA journal_mode').fetchone()[0],'wal');self.assertEqual(c.execute('PRAGMA integrity_check').fetchone()[0],'ok')
 def test_02_replay_before_full(self):
  id=self.create();self.create('two');self.create('three');self.assertEqual(self.q.submit('alpha','four',payload())[0],429);status,b=self.q.submit('alpha','one',payload());self.assertEqual(status,200);self.assertEqual(b['job']['id'],id);self.assertTrue(b['replayed'])
 def test_03_conflict(self):
  id=self.create();self.assertEqual(self.q.submit('alpha','one',payload('changed'))[0],409);self.assertEqual(self.q.get('alpha',id)[1]['job']['state'],'queued')
 def test_04_validate_payload(self):
  for p in [None,True,[],{}, {'kind':'report','text':True},{'kind':'other','text':'x'},{'kind':'report','text':' '},{'kind':'report','text':'x'*257},{'kind':'report','text':'x','owner':'beta'}]:self.assertEqual(self.q.submit('alpha','bad',p)[0],422)
  self.assertEqual(self.q.events('alpha',0)[1]['events'],[])
 def test_05_validate_key(self):
  for k in [None,True,'','a b','x'*65,'é','x\n']:self.assertEqual(self.q.submit('alpha',k,payload())[0],422)
 def test_06_owner_scope(self):
  id=self.create();other=self.create(owner='beta');self.assertNotEqual(id,other);self.assertEqual(self.q.get('beta',id)[0],404);self.assertEqual(self.q.cancel('beta',id)[0],404);self.assertEqual(self.q.artifact('beta',id)[0],404)
 def test_07_queued_cancel(self):
  id=self.create();self.assertEqual(self.q.cancel('alpha',id)[1]['job']['state'],'cancelled');self.q.start();time.sleep(.1);self.assertEqual(self.s.calls,[]);self.assertEqual(self.q.artifact('alpha',id)[0],409)
 def test_08_cancel_requested_is_not_stopped(self):
  self.s.hold.clear();id=self.create();self.q.start();self.assertTrue(self.s.entered.wait(2));self.assertEqual(self.q.cancel('alpha',id)[1]['job']['state'],'cancel_requested');self.s.hold.set();self.wait(id);self.assertIn(id,self.s.receipts);self.assertEqual(self.q.cancel('alpha',id)[1]['job']['state'],'completed')
 def test_09_unknown_ack_lookup_same_id(self):
  self.s.unknown=True;id=self.create();self.q.start();self.wait(id);self.assertEqual(self.s.calls.count(('execute',id)),1);self.assertIn(('lookup',id),self.s.calls);states=[e['state'] for e in self.q.events('alpha',0)[1]['events']];self.assertIn('reconciling',states)
 def test_10_unavailable_lookup_not_false_completed(self):
  self.s.unknown=True;self.s.offline=True;id=self.create();self.q.start();time.sleep(.7);self.assertIn(self.q.get('alpha',id)[1]['job']['state'],['reconciling','cancel_requested']);self.assertEqual(self.q.artifact('alpha',id)[0],409);self.assertLessEqual(self.s.calls.count(('execute',id)),1);self.assertLessEqual(self.s.calls.count(('lookup',id)),3)
 def test_11_cursor_and_schema(self):
  id=self.create();self.q.start();self.wait(id);events=self.q.events('alpha',0)[1]['events'];self.assertGreaterEqual(len(events),3);ids=[e['id'] for e in events];self.assertEqual(ids,sorted(set(ids)))
  for e in events:self.assertEqual(set(e),{'id','job_id','state'})
  self.assertEqual(self.q.events('alpha',ids[-1])[1]['events'],[])
  self.assertEqual(self.q.events('beta',0)[1]['events'],[])
 def test_12_invalid_cursor(self):
  for cursor in [-1,True,None,'0',.1]:self.assertEqual(self.q.events('alpha',cursor)[0],422)
 def test_13_artifact_exact_bytes(self):
  id=self.create(p=payload('  α\ntext  '));self.q.start();self.wait(id);self.assertEqual(self.q.artifact('alpha',id),(200,json.dumps(payload('  α\ntext  '),sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()))
 def test_14_concurrent_idempotency(self):
  barrier=threading.Barrier(8);out=[]
  def submit():barrier.wait();out.append(self.q.submit('alpha','parallel',payload()))
  threads=[threading.Thread(target=submit) for _ in range(8)]
  for t in threads:t.start()
  for t in threads:t.join(3)
  self.assertEqual(len(out),8);self.assertEqual(sum(s==202 for s,v in out),1);self.assertEqual({s for s,v in out},{200,202});self.assertEqual(len({v['job']['id'] for s,v in out}),1);self.q.start();self.wait(out[0][1]['job']['id']);self.assertEqual(sum(k=='execute' for k,id in self.s.calls),1)
 def test_15_lock_no_partial_event(self):
  before=self.q.events('alpha',0)[1]['events'];c=sqlite3.connect(str(Path(self.tmp.name)/'jobs.sqlite'));c.execute('BEGIN IMMEDIATE');started=time.monotonic()
  try:self.assertEqual(self.q.submit('alpha','locked',payload())[0],503);self.assertLess(time.monotonic()-started,1)
  finally:c.rollback();c.close()
  self.assertEqual(self.q.events('alpha',0)[1]['events'],before);self.create('locked')
 def test_16_drain_replay_persist(self):
  if os.environ.get('HARMONIKA_EXAM_PHASE')!='v2':self.skipTest('turn_b_only')
  id=self.create();self.assertEqual(self.q.drain(),(200,{'draining':True}));self.assertEqual(self.q.submit('alpha','other',payload())[0],503);self.assertEqual(self.q.submit('alpha','one',payload())[0],200);self.assertEqual(self.q.submit('alpha','one',payload('changed'))[0],409)
  self.q.stop();self.q=Queue(str(Path(self.tmp.name)/'jobs.sqlite'),self.s);self.assertEqual(self.q.submit('alpha','other',payload())[0],503);self.q.start();self.wait(id);self.assertEqual(self.q.resume(),(200,{'draining':False}));self.create('other')
if __name__=='__main__':unittest.main(verbosity=2)
