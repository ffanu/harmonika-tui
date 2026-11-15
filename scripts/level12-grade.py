"""Independent Level12 domain+public HTTP checks. Never rewrites candidate."""
import concurrent.futures,csv,hashlib,importlib.util,io,json,pathlib,sqlite3,sys,tempfile,time,unittest,uuid,fcntl
REPO=pathlib.Path(__file__).resolve().parents[1];R=pathlib.Path(pathlib.Path('/tmp/harmonika-level12-root.path').read_text().strip());P=R/'project'
NATIVE_LOCK=(R/'native.lock').open('a');fcntl.flock(NATIVE_LOCK,fcntl.LOCK_EX|fcntl.LOCK_NB)
META=json.loads((R/'project-meta.json').read_text())
if not all(hashlib.sha256((P/name).read_bytes()).hexdigest()==sha for name,sha in META['protected'].items()):raise RuntimeError('protected_candidate_changed')
def load(name,path):
 spec=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m
C=load('candidate_crm',P/'crm.py');O=load('private_controller',REPO/'scripts/level12-lab/controller.py');ADMIN={'username':'admin','role':'admin'};A={'username':'staff-a','role':'staff'};B={'username':'staff-b','role':'staff'}
class Domain(unittest.TestCase):
 def setUp(self):self.t=tempfile.TemporaryDirectory();self.db=pathlib.Path(self.t.name)/'crm.sqlite';self.s=C.CRMStore(self.db)
 def tearDown(self):self.t.cleanup()
 def error(self,status,fn,code=None):
  with self.assertRaises(C.AppError) as cm:fn()
  self.assertEqual(cm.exception.status,status)
  if code:self.assertEqual(cm.exception.code,code)
 def customer(self,p=A,key='c1',**kw):return self.s.create_customer(p,key,{'name':'Alpha','email':'a@example.invalid','stage':'lead',**kw})['customer']
 def test_identity_and_scope(self):
  c=self.customer();self.assertEqual(len(c['id']),32);self.assertEqual(c['owner'],'staff-a');self.assertEqual(self.s.customers(B),[]);self.error(404,lambda:self.s.customer(B,c['id']));self.error(404,lambda:self.s.update_customer(B,c['id'],1,{'name':'No'}));self.error(403,lambda:self.s.customers({'username':'staff-a','role':'admin'}));self.assertEqual(self.s.dashboard(B),dict(customers=0,active=0,open_tasks=0,draft_articles=0,published_articles=0))
 def test_normalized_replay_and_actor_kind(self):
  c=self.customer(name=' Alpha ');same=self.s.create_customer(A,'c1',{'name':'Alpha','email':'a@example.invalid','stage':'lead','owner':'staff-a'});self.assertTrue(same['replayed']);self.assertEqual(same['customer']['id'],c['id']);self.customer(B);t=self.s.create_task(A,'c1',{'customer_id':c['id'],'title':'Call'});self.assertFalse(t['replayed']);self.s.update_customer(A,c['id'],1,{'stage':'active'});self.assertEqual(self.s.create_customer(A,'c1',{'name':'Alpha','email':'a@example.invalid','stage':'lead'})['customer']['revision'],2);self.error(409,lambda:self.customer(name='Other'));self.s.delete_customer(A,c['id'],2);self.error(409,lambda:self.customer())
 def test_cas_validation(self):
  c=self.customer()
  for rev in [True,False,0,-1,1.0,'1',None]:self.error(422,lambda rev=rev:self.s.update_customer(A,c['id'],rev,{'name':'B'}))
  for patch in [{},{'owner':'admin'},{'name':False},{'name':' '},{'email':'a\nb@c'},{'stage':'invalid'},{'extra':1}]:self.error(422,lambda patch=patch:self.s.update_customer(A,c['id'],1,patch))
  self.s.update_customer(A,c['id'],1,{'name':'Beta'});self.error(409,lambda:self.s.update_customer(A,c['id'],1,{'name':'Gamma'}));self.assertEqual(self.s.customer(A,c['id'])['name'],'Beta')
 def test_filter_csv_and_literals(self):
  c=self.customer(name='=EXAM-SENTINEL');self.customer(key='c2',name='<script>alert(1)</script>',email='b@example.invalid',stage='active');self.assertEqual(len(self.s.customers(A,query='EXAM-sentinel')),1);self.assertEqual(len(self.s.customers(A,stage='active')),1);rows=list(csv.reader(io.StringIO(self.s.export_customers(A).decode())));self.assertEqual(rows[0],['id','name','email','stage','owner','revision']);self.assertIn("'=EXAM-SENTINEL",[r[1] for r in rows]);self.assertEqual(self.s.mechanics(),{'customers':2,'tasks':0,'articles':0})
 def test_task_authorization_and_cascade(self):
  c=self.customer();self.error(404,lambda:self.s.create_task(B,'task',{'customer_id':c['id'],'title':'No'}));t=self.s.create_task(ADMIN,'task',{'customer_id':c['id'],'title':'Call'})['task'];self.assertEqual(t['owner'],'staff-a');self.error(422,lambda:self.s.update_task(A,t['id'],1,{'customer_id':c['id']}));self.error(422,lambda:self.s.create_task(A,'task2',{'customer_id':c['id'],'title':'Call','owner':'admin'}));self.s.update_task(A,t['id'],1,{'status':'done'});self.error(409,lambda:self.s.delete_task(A,t['id'],1));self.s.delete_customer(A,c['id'],1);self.assertEqual(self.s.tasks(ADMIN),[])
 def test_articles(self):
  a=self.s.create_article(A,'a1',{'title':'Draft','body':'<script>literal</script>'})['article'];self.assertEqual(self.s.public_articles(),[]);self.error(404,lambda:self.s.article(B,a['id']));self.error(403,lambda:self.s.update_article(A,a['id'],1,{'published':True}));self.error(403,lambda:self.s.create_article(A,'a2',{'title':'No','body':'No','published':True}));self.s.update_article(ADMIN,a['id'],1,{'published':True});self.error(403,lambda:self.s.update_article(A,a['id'],2,{'title':'No'}));self.error(403,lambda:self.s.delete_article(A,a['id'],2));self.assertEqual(self.s.public_articles(),[{'id':a['id'],'title':'Draft','body':'<script>literal</script>'}])
 def test_real_lock(self):
  c=sqlite3.connect(self.db);c.execute('BEGIN IMMEDIATE');start=time.monotonic()
  try:self.error(503,lambda:self.customer(),code='sqlite_busy');self.assertLess(time.monotonic()-start,1)
  finally:c.rollback();c.close()
  self.customer();self.assertEqual(self.s.mechanics()['customers'],1)
 def test_concurrent_idempotency(self):
  def one(_):
   for retry in range(12):
    try:return C.CRMStore(self.db).create_customer(A,'race',{'name':'Race','email':'race@example.invalid','stage':'lead'})['customer']['id']
    except C.AppError as e:
     if e.status!=503:raise
     time.sleep(.05)
   raise AssertionError('concurrent did not settle')
  with concurrent.futures.ThreadPoolExecutor(max_workers=5) as ex:ids=list(ex.map(one,range(5)))
  self.assertEqual(len(set(ids)),1);self.assertEqual(self.s.mechanics()['customers'],1)
 def test_schema_compatibility(self):
  c=self.customer()
  with sqlite3.connect(self.db) as conn:conn.execute("ALTER TABLE customers ADD COLUMN segment TEXT NOT NULL DEFAULT 'general'");conn.execute('PRAGMA user_version=2')
  second=C.CRMStore(self.db);self.assertEqual(second.customer(A,c['id'])['name'],'Alpha');self.assertEqual(set(second.customer(A,c['id'])),{'id','name','email','stage','owner','revision'});second.update_customer(A,c['id'],1,{'stage':'active'})
  with sqlite3.connect(self.db) as conn:self.assertEqual(conn.execute('PRAGMA user_version').fetchone()[0],2);self.assertEqual(conn.execute('SELECT segment FROM customers').fetchone()[0],'general');conn.execute('PRAGMA user_version=99')
  before=hashlib.sha256(self.db.read_bytes()).hexdigest();self.error(503,lambda:C.CRMStore(self.db),code='schema_unsupported');self.assertEqual(before,hashlib.sha256(self.db.read_bytes()).hexdigest())
class HTTP(unittest.TestCase):
 def api(self,method,path,body=None,role='admin',mode=None,key=None):return O.api({'method':method,'path':path,'body':body,'role':role,'auth_mode':mode,'key':key})
 def test_invalid_http_contract(self):
  key='invalid-'+uuid.uuid4().hex
  self.assertEqual(self.api('POST','/api/customers',[],key=key)['status'],422)
  self.assertEqual(self.api('POST','/api/customers',{'name':'Missing key','email':'invalid@example.invalid','stage':'lead'})['status'],422)
  self.assertEqual(self.api('POST','/api/customers',{'name':'x'*33000,'email':'invalid@example.invalid','stage':'lead'},key=key)['status'],413)
  self.assertEqual(self.api('POST','/api/customers',{'name':'x','email':'invalid@example.invalid','stage':'lead','role':'admin'},key=key)['status'],422)
  self.assertEqual(self.api('POST','/api/articles',{'title':'No','body':'No','published':True},role='staff-a',key=key)['status'],403)
  self.assertEqual(self.api('GET','/readyz',role='anonymous')['status'],200)
 def test_live(self):
  self.assertEqual(self.api('GET','/healthz',role='anonymous')['status'],200);self.assertEqual(self.api('GET','/api/customers',role='anonymous')['status'],401)
  key='grade-'+uuid.uuid4().hex;body={'name':'HTTP EXAM-SENTINEL','email':'http@example.invalid','stage':'lead'};r=self.api('POST','/api/customers',body,'staff-a',key=key);self.assertEqual(r['status'],201);id=r['body']['customer']['id'];self.assertEqual(self.api('POST','/api/customers',body,'staff-a',key=key)['status'],200);self.assertEqual(self.api('GET','/api/customers/'+id,role='staff-b')['status'],404)
  for mode in ['missing_csrf','invalid_csrf','foreign_origin','forged']:self.assertEqual(self.api('PATCH','/api/customers/'+id,{'revision':1,'name':'No'},'staff-a',mode)['status'],403)
  self.assertEqual(self.api('PATCH','/api/customers/'+id,{'revision':1,'name':'HTTP Updated'},'staff-a')['status'],200);self.assertEqual(self.api('PATCH','/api/customers/'+id,{'revision':1,'name':'No'},'staff-a')['status'],409)
  result=self.api('GET','/api/customers/export.csv',role='staff-a');self.assertEqual(result['status'],200);self.assertEqual(result['headers'].get('Content-Disposition'),'attachment; filename="customers.csv"');self.assertIn('HTTP Updated',result['body'])
  for path in ['/app.py','/security.py','/.env','/crm.sqlite','/static/../../auth-hashes.json']:self.assertEqual(self.api('GET',path,role='anonymous')['status'],404)
  headers=self.api('GET','/healthz',role='anonymous')['headers'];self.assertIn("default-src 'self'",headers.get('Content-Security-Policy',''));self.assertEqual(headers.get('X-Content-Type-Options'),'nosniff');self.assertIn('max-age=',headers.get('Strict-Transport-Security',''))
if __name__=='__main__':
 suite=unittest.defaultTestLoader.loadTestsFromTestCase(Domain)
 if '--http' in sys.argv:suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(HTTP))
 runner=unittest.TextTestRunner(verbosity=2);result=runner.run(suite);out={'passed':result.wasSuccessful(),'cases':result.testsRun,'failures':len(result.failures),'errors':len(result.errors),'at':time.time()};(R/('independent-'+str(time.time_ns())+'.json')).write_text(json.dumps(out,indent=2));sys.exit(not result.wasSuccessful())
