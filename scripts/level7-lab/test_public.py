import tempfile,time,unittest
from pathlib import Path
from engine import Queue
from lab_fixture import MemorySink
class Public(unittest.TestCase):
 def test_replay_and_owner(self):
  with tempfile.TemporaryDirectory() as d:
   s=MemorySink();q=Queue(str(Path(d)/'jobs.sqlite'),s,worker_id='worker1',lease_seconds=3)
   try:
    p={'kind':'report','text':'fixture'};a,v=q.submit('alpha','same',p);self.assertEqual(a,202);id=v['job']['id'];self.assertEqual(q.submit('alpha','same',p)[0],200);self.assertEqual(q.lookup_request('alpha','same')[1]['job']['id'],id);self.assertEqual(q.get('beta',id)[0],404);q.start()
    for _ in range(100):
     if q.get('alpha',id)[1]['job']['state']=='completed':break
     time.sleep(.025)
    self.assertEqual(q.get('alpha',id)[1]['job']['state'],'completed');self.assertEqual(len(s.effects),1)
   finally:q.stop()
 def test_invalid_is_atomic(self):
  with tempfile.TemporaryDirectory() as d:
   q=Queue(str(Path(d)/'jobs.sqlite'),MemorySink(),worker_id='worker1')
   try:self.assertEqual(q.submit('alpha','bad',{'kind':'report','text':True})[0],422);self.assertEqual(q.events('alpha',0)[1]['events'],[])
   finally:q.stop()
if __name__=='__main__':unittest.main()
