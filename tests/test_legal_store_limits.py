"""Real-SQLite store bound regressions: quota-bounded retrieval, case scoping, atomic quota errors, structured busy."""
import os,sqlite3,tempfile,unittest
from pathlib import Path
try:
 from backend.legal_engine.common import DomainError
 from backend.legal_engine.store import Store,Context,RECORD_QUOTA,LIMITS,limit_mismatches
 try:from backend.legal_engine.evidence import MAX_CUSTODY_ENTRIES as CLAIMED_CUSTODY
 except ImportError:CLAIMED_CUSTODY=10000
 try:
  from backend.legal_engine.runtime import Runtime
  RUNTIME=True
 except ImportError:RUNTIME=False
 STORE=True
except ImportError:STORE=False
ACTOR='api-token:901'
ACTOR_B='api-token:902'
SENTINEL='PRIVATE_STORE_LIMIT_SENTINEL'
class FakeSources:
 def search(self,query,jurisdiction='ID',limit=10,kind=None):return []
 def get(self,id):raise DomainError('source_not_found',404)
 def history(self,id):return [self.get(id)]
def make_store(tmp):
 return Store(Path(tmp.name)/'state.sqlite',os.urandom(32))
@unittest.skipUnless(STORE,'legal-specific encrypted store venv required; mandatory suite run separately')
class Bound(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  cls.tmp=tempfile.TemporaryDirectory();cls.addClassCleanup(cls.tmp.cleanup)
  cls.store=make_store(cls.tmp)
  cls.ids={'case-a':[],'case-b':[],'case-c':[]}
  with cls.store.db() as d:
   c=Context(cls.store,d,ACTOR)
   for cid,n in (('case-a',1200),('case-b',200),('case-c',100)):
    for i in range(n):cls.ids[cid].append(c.create('legal_message',{'case_id':cid,'sequence':i+1,'role':'user','content':SENTINEL+' '+cid})['id'])
 def test_default_list_supports_full_tenant_quota_over_1000(self):
  with self.store.db() as d:
   c=Context(self.store,d,ACTOR)
   rows=c.list('legal_message')
  self.assertEqual(len(rows),1500)
  self.assertEqual({r['id'] for r in rows},set(self.ids['case-a'])|set(self.ids['case-b'])|set(self.ids['case-c']))
  self.assertTrue(all(r['content'].startswith(SENTINEL) for r in rows))
  raw=b''.join(p.read_bytes() for p in Path(self.tmp.name).glob('state.sqlite*'))
  self.assertNotIn(SENTINEL.encode(),raw)
 def test_case_scoped_list_not_bricked_or_truncated(self):
  with self.store.db() as d:
   c=Context(self.store,d,ACTOR)
   a=c.list('legal_message',where={'case_id':'case-a'})
   b=c.list('legal_message',where={'case_id':'case-b'})
   cs=c.list('legal_message',where={'case_id':'case-c'})
  self.assertEqual({r['id'] for r in a},set(self.ids['case-a']))
  self.assertEqual(len(a),1200)
  self.assertTrue(all(r['case_id']=='case-a' for r in a))
  self.assertEqual(sorted(r['sequence'] for r in b),list(range(1,201)))
  self.assertEqual(len(cs),100)
 def test_where_applies_before_cap_raises_overcap_without_truncation(self):
  with self.store.db() as d:
   c=Context(self.store,d,ACTOR)
   kept=c.list('legal_message',where={'case_id':'case-b'},limit=200)
   with self.assertRaises(DomainError) as e:c.list('legal_message',where={'case_id':'case-b'},limit=199)
   self.assertEqual(e.exception.code,'query_limit_exceeded');self.assertEqual(e.exception.status,413)
   with self.assertRaises(DomainError) as e2:c.list('legal_message',limit=1000)
   self.assertEqual(e2.exception.code,'query_limit_exceeded')
  self.assertEqual(len(kept),200)
 def test_explicit_limit_bounds_match_actual_tenant_quota(self):
  with self.store.db() as d:
   c=Context(self.store,d,ACTOR)
   self.assertEqual(len(c.list('legal_message',limit=RECORD_QUOTA)),1500)
   for bad in (0,-1,RECORD_QUOTA+1,'5',True,1.5):
    with self.assertRaises(DomainError) as e:c.list('legal_message',limit=bad)
    self.assertEqual(e.exception.code,'invalid_limit')
 def test_update_still_versioned_on_large_kind(self):
  with self.store.db() as d:
   c=Context(self.store,d,ACTOR)
   mid=self.ids['case-c'][0];out=c.update('legal_message',mid,{'role':'assistant'})
   self.assertEqual(out['version'],2)
   self.assertEqual(c.get('legal_message',mid)['role'],'assistant')
 def test_scan_bound_mismatch_reported_separately_not_as_quota(self):
  t=self.store.tenant(ACTOR_B)
  with self.store.db() as d:
   for i in range(RECORD_QUOTA+1):
    rid='n%05d'%i
    d.execute('INSERT INTO records VALUES(?,?,?,?,?)',(t,'legal_note',rid,1,self.store.seal({'n':i},'legal.record:'+t+':legal_note:'+rid)))
  with self.store.db() as d:
   c=Context(self.store,d,ACTOR_B)
   with self.assertRaises(DomainError) as e:c.list('legal_note')
  self.assertEqual(e.exception.code,'store_bound_mismatch')
  self.assertEqual(e.exception.status,413)
  self.assertNotIn(e.exception.code,('record_quota','query_limit_exceeded'))
  self.assertEqual(e.exception.details['enforced_bound'],RECORD_QUOTA)
  self.assertGreaterEqual(e.exception.details['tenant_kind_rows'],RECORD_QUOTA+1)
 def test_claim_over_actual_quota_reported_as_unsupported_mismatch(self):
  self.assertLessEqual(CLAIMED_CUSTODY,LIMITS['records_per_tenant'])
  historical_claim=10000
  self.assertEqual(LIMITS['list_limit_max'],RECORD_QUOTA)
  m=limit_mismatches({'evidence.chain.custody_entries':historical_claim})
  self.assertEqual(len(m),1)
  self.assertEqual(m[0],{'claim':'evidence.chain.custody_entries','claimed_bound':historical_claim,'enforced_bound':RECORD_QUOTA,'supported':False})
  self.assertEqual(limit_mismatches({'legal_messages_per_tenant':RECORD_QUOTA}),[])
  with self.assertRaises(DomainError):limit_mismatches('x')
@unittest.skipUnless(STORE,'legal-specific encrypted store venv required; mandatory suite run separately')
class Quota(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
  self.store=make_store(self.tmp)
  with self.store.db() as d:
   c=Context(self.store,d,ACTOR)
   self.anchor=c.create('legal_case',{'topic':'anchor','facts':'x'})['id']
   for i in range(RECORD_QUOTA-2):c.create('legal_message',{'case_id':self.anchor,'sequence':i+1,'role':'user','content':'m%d'%i})
 def test_quota_error_structured_atomic_and_per_tenant(self):
  with self.assertRaises(DomainError) as e:
   with self.store.db() as d:
    c=Context(self.store,d,ACTOR)
    c.create('legal_case',{'topic':'tx-partial','facts':'x'})
    c.create('legal_case',{'topic':'tx-over','facts':'x'})
  self.assertEqual(e.exception.code,'record_quota')
  self.assertEqual(e.exception.status,429)
  self.assertEqual(e.exception.details['record_quota'],RECORD_QUOTA)
  self.assertGreaterEqual(e.exception.details['tenant_records'],RECORD_QUOTA)
  with self.store.db() as d:
   c=Context(self.store,d,ACTOR)
   self.assertEqual(d.execute('SELECT count(*) FROM records WHERE tenant=?',(c.tenant,)).fetchone()[0],RECORD_QUOTA-1)
   self.assertEqual([r['id'] for r in c.list('legal_case')],[self.anchor])
   self.assertEqual(c.get('legal_case',self.anchor)['topic'],'anchor')
   self.assertEqual(len(c.list('legal_message')),RECORD_QUOTA-2)
   other=Context(self.store,d,ACTOR_B)
   self.assertTrue(other.create('legal_case',{'topic':'tenant-b','facts':'x'})['id'])
@unittest.skipUnless(STORE,'legal-specific encrypted store venv required; mandatory suite run separately')
class Busy(unittest.TestCase):
 def test_writer_lock_maps_to_structured_legal_busy_then_recovers(self):
  tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
  store=make_store(tmp);store.timeout=0.05
  blocker=sqlite3.connect(store.path,timeout=0.05)
  try:
   blocker.execute('BEGIN IMMEDIATE')
   with self.assertRaises(DomainError) as e:store.transaction(lambda d:d.execute('SELECT count(*) FROM records').fetchone())
   self.assertEqual(e.exception.code,'legal_busy')
   self.assertEqual(e.exception.status,429)
   self.assertEqual(e.exception.details,{'reason':'database_busy'})
  finally:
   blocker.rollback();blocker.close()
  with store.db() as d:
   c=Context(store,d,ACTOR)
   self.assertTrue(c.create('legal_case',{'topic':'after','facts':'x'})['id'])
@unittest.skipUnless(STORE and RUNTIME,'legal-specific encrypted runtime venv required; mandatory suite run separately')
class RuntimeScope(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
  self.rt=Runtime(self.tmp.name,os.urandom(32),FakeSources())
  self.a=RuntimeScope.actor()
  ra=self.rt.case_create(self.a,{'topic':'kasus a','facts':'fakta a'},'ka');self.assertEqual(ra['status'],'completed',ra)
  rb=self.rt.case_create(self.a,{'topic':'kasus b','facts':'fakta b'},'kb');self.assertEqual(rb['status'],'completed',rb)
  self.case_a=ra['result']['case']['id'];self.case_b=rb['result']['case']['id']
  with self.rt.store.db() as d:
   c=Context(self.rt.store,d,self.a)
   for i in range(1100):c.create('legal_message',{'case_id':self.case_a,'sequence':i+1,'role':'user','content':'m%d'%i})
   for i in range(5):c.create('legal_message',{'case_id':self.case_b,'sequence':i+1,'role':'user','content':'b%d'%i})
 @staticmethod
 def actor():return 'api-token:910'
 def test_case_context_not_bricked_over_1000_kind_rows(self):
  with self.rt.store.db() as d:
   c=Context(self.rt.store,d,self.a)
   self.assertEqual(len(c.list('legal_message')),1105)
  r=self.rt.case_context(self.a,self.case_a)
  self.assertEqual(len(r['messages']),40)
  self.assertEqual([m['sequence'] for m in r['messages']],list(range(1061,1101)))
  self.assertTrue(all(m['case_id']==self.case_a for m in r['messages']))
  self.assertEqual(r['evidence'],[]);self.assertEqual(r['media_analyses'],[])
  rb=self.rt.case_context(self.a,self.case_b)
  self.assertEqual(len(rb['messages']),5)
if __name__=='__main__':unittest.main()
