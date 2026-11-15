import pathlib,sqlite3,tempfile,unittest
from crm import CRMStore,AppError
ADMIN={'username':'admin','role':'admin'};A={'username':'staff-a','role':'staff'};B={'username':'staff-b','role':'staff'}
class PublicContract(unittest.TestCase):
 def setUp(self):self.t=tempfile.TemporaryDirectory();self.path=pathlib.Path(self.t.name)/'crm.sqlite';self.s=CRMStore(self.path)
 def tearDown(self):self.t.cleanup()
 def err(self,status,fn):
  with self.assertRaises(AppError) as cm:fn()
  self.assertEqual(cm.exception.status,status)
 def create(self,key='one',**patch):return self.s.create_customer(A,key,{'name':'Alpha','email':'a@example.invalid','stage':'lead',**patch})['customer']
 def test_role_scope(self):
  c=self.create();self.assertEqual(self.s.customers(B),[]);self.err(404,lambda:self.s.customer(B,c['id']));self.assertEqual(len(self.s.customers(ADMIN)),1)
 def test_replay_after_edit(self):
  c=self.create();self.s.update_customer(A,c['id'],1,{'stage':'active'});r=self.s.create_customer(A,'one',{'name':'Alpha','email':'a@example.invalid','stage':'lead'});self.assertTrue(r['replayed']);self.assertEqual(r['customer']['stage'],'active');self.err(409,lambda:self.create(name='Other'))
 def test_deleted_replay(self):
  c=self.create();self.s.delete_customer(A,c['id'],1);self.err(409,lambda:self.create())
 def test_cas_and_types(self):
  c=self.create();self.err(422,lambda:self.s.update_customer(A,c['id'],True,{'name':'Beta'}));self.s.update_customer(A,c['id'],1,{'name':'Beta'});self.err(409,lambda:self.s.update_customer(A,c['id'],1,{'name':'Gamma'}))
 def test_tasks_owner_cascade(self):
  c=self.create();t=self.s.create_task(ADMIN,'task',{'customer_id':c['id'],'title':'Call'})['task'];self.assertEqual(t['owner'],'staff-a');self.err(404,lambda:self.s.task(B,t['id']));self.s.delete_customer(A,c['id'],1);self.assertEqual(self.s.tasks(ADMIN),[])
 def test_article_publish(self):
  art=self.s.create_article(A,'draft',{'title':'News','body':'Example'})['article'];self.assertEqual(self.s.public_articles(),[]);self.err(403,lambda:self.s.update_article(A,art['id'],1,{'published':True}));self.s.update_article(ADMIN,art['id'],1,{'published':True});self.assertEqual(set(self.s.public_articles()[0]),{'id','title','body'});self.err(403,lambda:self.s.delete_article(A,art['id'],2))
 def test_formula_csv(self):
  self.create(name='=1+1');out=self.s.export_customers(A);self.assertIsInstance(out,bytes);self.assertIn(b"'=1+1",out);self.assertTrue(out.startswith(b'id,name,email,stage,owner,revision\r\n'))
 def test_wal(self):
  with sqlite3.connect(self.path) as c:self.assertEqual(c.execute('PRAGMA journal_mode').fetchone()[0],'wal');self.assertEqual(c.execute('PRAGMA user_version').fetchone()[0],1)
 def test_bad_input(self):
  self.err(422,lambda:self.create(name=False));self.err(422,lambda:self.create(unknown=True));self.err(403,lambda:self.s.customers({'username':'staff-a','role':'admin'}))
if __name__=='__main__':unittest.main()
