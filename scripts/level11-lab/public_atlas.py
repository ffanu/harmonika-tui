import unittest,tempfile,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from workspace import WorkspaceStore
class Public(unittest.TestCase):
 def setUp(self):self.tmp=tempfile.TemporaryDirectory();self.q=WorkspaceStore(Path(self.tmp.name)/'app.sqlite')
 def tearDown(self):self.tmp.cleanup()
 def test_ticket_create(self):self.assertEqual(self.q.create_ticket('alpha','one','one',{'title':'Hello','status':'open'})[0],201)
 def test_job_replay(self):
  a=self.q.submit('alpha','one','same',{'units':2});b=self.q.submit('alpha','one','same',{'units':2});self.assertEqual(a[0],202);self.assertEqual(b[0],200);self.assertEqual(a[1]['job']['id'],b[1]['job']['id'])
 def test_bad_boolean(self):self.assertEqual(self.q.submit('alpha','one','bad',{'units':True})[0],422)
 def test_scope(self):
  a=self.q.submit('alpha','one','same',{'units':2})[1]['job']['id'];self.assertEqual(self.q.get('beta','one',a)[0],404);self.assertEqual(self.q.get('alpha','two',a)[0],404)
