import json,sqlite3,tempfile,unittest
from pathlib import Path
from backend.unified_engine.state import State,Error
class UnifiedState(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.path=Path(self.temp.name)/'private/state.sqlite';self.s=State(self.path,b'x'*32);self.a='api-token:1';self.b='api-token:2';self.payload={'messages':[{'role':'user','content':'synthetic-private-value'}]}
 def test_idempotency_exact_and_principal_isolation(self):
  j=self.s.create(self.a,self.payload,'request-0001');self.assertTrue(self.s.create(self.a,self.payload,'request-0001')['replayed']);self.assertEqual(j['id'],self.s.create(self.a,self.payload,'request-0001')['id'])
  with self.assertRaisesRegex(Error,'not_found'):self.s.get(self.b,j['id'])
  with self.assertRaisesRegex(Error,'idempotency_conflict'):self.s.create(self.a,{'messages':[]},'request-0001')
 def test_encryption_disk_not_plaintext(self):
  j=self.s.create(self.a,self.payload,'request-0001');self.assertNotIn(b'synthetic-private-value',self.path.read_bytes());self.assertNotIn(b'api-token:1',self.path.read_bytes());self.assertNotIn('request',self.s.public(j))
 def test_busy_and_terminal_state_wins(self):
  j=self.s.create(self.a,self.payload,'request-0001')
  with self.assertRaisesRegex(Error,'actor_busy'):self.s.create(self.a,self.payload,'request-0002')
  self.s.transition(self.a,j['id'],'stopped');self.s.transition(self.a,j['id'],'completed',{'text':'late'})
  self.assertEqual(self.s.get(self.a,j['id'])['status'],'stopped');self.assertIsNone(self.s.get(self.a,j['id'])['result'])
 def test_restart_no_reexecution(self):
  j=self.s.create(self.a,self.payload,'request-0001');self.s.transition(self.a,j['id'],'running');new=State(self.path,b'x'*32);self.assertEqual(new.recover(),1);self.assertEqual(new.get(self.a,j['id'])['status'],'recovery_required');self.assertEqual(len(new.get(self.a,j['id'])['events']),3)
 def test_session_persistence_and_scope(self):
  s=self.s.session(self.a);self.s.append(self.a,s['id'],'router','alat jaringan');new=State(self.path,b'x'*32);self.assertEqual(new.session(self.a,s['id'])['messages'][-1]['content'],'alat jaringan')
  with self.assertRaisesRegex(Error,'not_found'):new.session(self.b,s['id'])
 def test_invalid_principal_key_and_id(self):
  for a in ('root','api-token:0','api-token:1/../../etc'):
   with self.assertRaises(Error):self.s.owner(a)
  with self.assertRaises(Error):self.s.create(self.a,self.payload,'short')
  with self.assertRaises(Error):self.s.get(self.a,'../../etc')
 def test_ttl_and_quotas(self):
  j=self.s.create(self.a,self.payload,'request-0001')
  with self.s.store._transaction() as db:db.execute('UPDATE unified_jobs SET expires=0')
  with self.assertRaisesRegex(Error,'not_found'):self.s.get(self.a,j['id'])
  for _ in range(10):self.s.session(self.a)
  with self.assertRaisesRegex(Error,'session_quota'):self.s.session(self.a)
 def test_session_bound_and_encrypted(self):
  id=self.s.session(self.a)['id']
  for n in range(15):self.s.append(self.a,id,str(n)*10000,'answer'*2000)
  s=self.s.session(self.a,id);self.assertLessEqual(len(s['messages']),20);self.assertLessEqual(len(json.dumps(s).encode()),48000)
 def test_admission_capacity_does_not_prevent_terminal_result(self):
  from unittest.mock import patch
  j=self.s.create(self.a,self.payload,'request-0001')
  with patch('backend.unified_engine.state.ADMISSION_BYTES',0):
   with self.assertRaisesRegex(Error,'state_capacity'):self.s.create(self.b,self.payload,'request-0002')
   self.assertEqual(self.s.transition(self.a,j['id'],'completed',{'text':'terminal result'})['status'],'completed')
if __name__=='__main__':unittest.main()
