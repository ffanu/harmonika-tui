import unittest,sys,os,json,copy
sys.path.insert(0,os.environ['HARMONIKA_EXAM_PROJECT']+'/src')
from fairqueue import Queue,QueueFull,IdempotencyConflict
class Followup(unittest.TestCase):
 def test_per_tenant_capacity_with_global_spare(self):
  for state in ['running','cancel_requested','unknown']:
   q=Queue(max_running=3,lease_seconds=1);q.submit('a','1',{},0);q.submit('a','2',{},0);q.submit('b','1',{},0);a=q.acquire(0);q.acquire(0)
   if state=='cancel_requested':q.cancel('a',a['id'],0)
   if state=='unknown':q.poll('a',a['id'],1)
   self.assertIsNone(q.acquire(1 if state=='unknown' else 0))
 def test_immediate_expiry_admitted_when_waiting_full(self):
  q=Queue(max_waiting=1);q.submit('a','a',{},0);self.assertEqual(q.submit('b','b',{},0,0)['state'],'expired')
  with self.assertRaises(QueueFull):q.submit('c','c',{},0)
 def test_audit_duplicate_rejection_expiry_and_private_key(self):
  q=Queue(max_waiting=1);a=q.submit('private-tenant','private-key',{'private_note':'fixture-data'},0,1);n=len(q.audit());q.submit('private-tenant','private-key',{'private_note':'fixture-data'},0,1);self.assertEqual(len(q.audit()),n)
  with self.assertRaises(QueueFull):q.submit('b','b',{},0)
  self.assertEqual(len(q.audit()),n);q.poll('private-tenant',a['id'],1);self.assertEqual(len(q.audit()),n+1);self.assertEqual(q.audit()[-1]['state'],'expired')
  for s in ['private-tenant','private-key','fixture-data']:self.assertNotIn(s,json.dumps(q.audit()))
 def test_clock_checked_on_all_timed_operations(self):
  for now in [1,True,float('inf'),float('nan')]:
   q=Queue();a=q.submit('a','a',{},2);before=q.audit()
   for fn in [lambda:q.submit('b','b',{},now),lambda:q.poll('a',a['id'],now),lambda:q.cancel('a',a['id'],now),lambda:q.ack('a',a['id'],'stale','completed',now)]:
    with self.assertRaises(ValueError):fn()
    self.assertEqual(q.audit(),before)
   q=Queue(lease_seconds=1);a=q.submit('a','a',{},0);q.acquire(0);q.poll('a',a['id'],2);before=q.audit()
   with self.assertRaises(ValueError):q.reconcile('a',a['id'],'completed',now)
   self.assertEqual(q.audit(),before)
 def test_submit_refreshes_running_lease(self):
  q=Queue(lease_seconds=1);a=q.submit('a','a',{},0);q.acquire(0);q.submit('b','b',{},1);self.assertEqual(q.poll('a',a['id'],1)['state'],'unknown');self.assertTrue(any(x['state']=='unknown' for x in q.audit()))
 def test_sequential_leases_missing_jobs_and_bad_lease_type(self):
  q=Queue();q.submit('a','a',{},0);a=q.acquire(0);q.ack('a',a['id'],a['lease'],'completed',0);q.submit('a','b',{},0);b=q.acquire(0);self.assertNotEqual(a['lease'],b['lease'])
  with self.assertRaises(ValueError):q.ack('a',b['id'],None,'completed',0)
  for fn in [lambda:q.poll('a','missing',0),lambda:q.cancel('a','missing',0),lambda:q.ack('a','missing','x','completed',0),lambda:q.reconcile('a','missing','completed',0)]:
   with self.assertRaises(KeyError):fn()
 def test_conflict_precedes_capacity(self):
  q=Queue(max_waiting=1);q.submit('a','k',{},0)
  with self.assertRaises(IdempotencyConflict):q.submit('a','k',{'changed':1},0)
 def test_unsuccessful_acquire_does_not_advance_fairness(self):
  q=Queue(max_running=1);q.submit('a','1',{},0);q.submit('b','1',{},0);q.submit('c','1',{},0);a=q.acquire(0);self.assertIsNone(q.acquire(0));q.ack('a',a['id'],a['lease'],'completed',0);self.assertEqual(q.acquire(0)['tenant'],'b')
if __name__=='__main__':unittest.main()
