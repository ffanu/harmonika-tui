import base64,hashlib,json,tempfile,time,unittest
from pathlib import Path
from engine import Queue
from effect_connector import EffectConnector
class Public(unittest.TestCase):
 def test_replay_and_artifact(self):
  with tempfile.TemporaryDirectory() as d:
   q=Queue(str(Path(d)/'jobs.sqlite'),EffectConnector(d));p={'kind':'report','text':'Synthetic α'}
   status,a=q.submit('alpha','one',p);self.assertEqual(status,202)
   status,b=q.submit('alpha','one',p);self.assertEqual(status,200);self.assertEqual(a['job']['id'],b['job']['id'])
   q.start()
   try:
    for _ in range(100):
     state=q.get('alpha',a['job']['id'])[1]['job']['state']
     if state=='completed':break
     time.sleep(.05)
    self.assertEqual(state,'completed');status,raw=q.artifact('alpha',a['job']['id']);self.assertEqual(status,200);self.assertEqual(raw,json.dumps(p,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode())
   finally:q.stop()
 def test_invalid_before_mutation(self):
  with tempfile.TemporaryDirectory() as d:
   q=Queue(str(Path(d)/'jobs.sqlite'),EffectConnector(d))
   self.assertEqual(q.submit('alpha','bad',{'kind':'report','text':True})[0],422)
   self.assertEqual(q.events('alpha',0)[1]['events'],[])
   q.stop()
if __name__=='__main__':unittest.main()
