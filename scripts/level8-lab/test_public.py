import json,os,tempfile,unittest
from pathlib import Path
from workflow_engine import WorkflowStore
class NoChild:
 def __getattr__(self,key):raise AssertionError('unexpected_child_io')
class Public(unittest.TestCase):
 def setUp(self):self.tmp=tempfile.TemporaryDirectory();self.q=WorkflowStore(str(Path(self.tmp.name)/'workflow.sqlite'),NoChild())
 def tearDown(self):self.q.stop();self.tmp.cleanup()
 def test_request_identity(self):
  p={'text':'synthetic','tasks':[{'name':'prepare','needs':[]}]};s,v=self.q.submit('alpha','one',p);self.assertEqual(s,202);id=v['workflow']['id'];self.assertEqual(self.q.submit('alpha','one',p)[0],200);self.assertEqual(self.q.lookup_request('alpha','one')[1]['workflow']['id'],id);self.assertEqual(self.q.get('beta',id)[0],404)
 def test_cycle_atomic(self):
  self.assertEqual(self.q.submit('alpha','cycle',{'text':'x','tasks':[{'name':'a','needs':['b']},{'name':'b','needs':['a']}]})[0],422);self.assertEqual(self.q.events('alpha',0)[1]['events'],[])
if __name__=='__main__':unittest.main()
