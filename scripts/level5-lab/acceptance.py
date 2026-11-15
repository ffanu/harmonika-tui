"""Independent black-box checks; never import candidate tests or expose cookies."""
import base64,concurrent.futures,http.cookiejar,json,os,sqlite3,sys,tempfile,threading,time,unittest,urllib.request,urllib.error,uuid
from pathlib import Path
BASE=os.environ['LEVEL5_URL'];V2=os.environ.get('LEVEL5_V2')=='1'
ORIGIN=os.environ.get('LEVEL5_EXAM_ORIGIN')
class Client:
 def __init__(self):
  self.jar=http.cookiejar.CookieJar();self.opener=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))
 def request(self,path,body=None,method=None,headers=None,raw=None):
  h={'Content-Type':'application/json',**({'X-Exam-Origin':ORIGIN} if ORIGIN else {}),**(headers or {})};data=raw if raw is not None else json.dumps(body).encode() if body is not None else None
  req=urllib.request.Request(BASE+path,data=data,method=method or ('POST' if data is not None else 'GET'),headers=h)
  try:r=self.opener.open(req,timeout=35)
  except urllib.error.HTTPError as e:r=e
  with r:
   data=r.read();hs=dict(r.headers);code=r.status
  try:value=json.loads(data) if data else None
  except ValueError:value=data
  return code,value,hs
 def login(self,owner):return self.request('/api/login',{'username':owner,'password':'demo-'+owner})
class Acceptance(unittest.TestCase):
 def setUp(self):
  self.a=Client();self.b=Client();self.assertEqual(self.a.login('alpha')[0],200);self.assertEqual(self.b.login('beta')[0],200);self.created=[]
 def tearDown(self):
  for c,id in self.created:
   try:c.request('/api/tickets/'+str(id),method='DELETE')
   except Exception:pass
 def make(self,c=None,title='Synthetic ticket',description='Synthetic description',key=None,**extra):
  c=c or self.a;key=key or 'test-'+uuid.uuid4().hex;payload={'title':title,'description':description,**extra};code,item,_=c.request('/api/tickets',payload,headers={'Idempotency-Key':key});self.assertEqual(code,201)
  self.created.append((c,item['id']));return key,item,payload
 def test_01_unauth_and_wrong_password(self):
  c=Client();self.assertEqual(c.request('/api/tickets')[0],401);self.assertEqual(c.request('/api/login',{'username':'alpha','password':'incorrect'})[0],401);self.assertEqual(c.request('/api/me')[0],401)
 def test_02_cookie_and_me(self):
  code,body,h=self.a.login('alpha');cookie=h.get('Set-Cookie','').lower();self.assertTrue('httponly' in cookie and 'samesite=strict' in cookie and 'path=/' in cookie);self.assertEqual(self.a.request('/api/me')[1],{'owner':'alpha'});self.assertGreaterEqual(len(list(self.a.jar)[0].value),24)
 def test_03_logout_revokes(self):
  stale={c.name:c.value for c in self.a.jar};code,body,_=self.a.request('/api/logout',{},method='POST');self.assertEqual(code,204);self.assertIsNone(body);self.assertEqual(self.a.request('/api/me',headers={'Cookie':'; '.join(k+'='+v for k,v in stale.items())})[0],401)
 def test_04_tenant_list_and_spoof(self):
  _,item,_=self.make(title='Alpha private '+uuid.uuid4().hex[:8],owner='beta');ids=[x['id'] for x in self.b.request('/api/tickets',headers={'X-Tenant-ID':'alpha'})[1]['tickets']];self.assertNotIn(item['id'],ids);self.assertEqual(self.a.request('/api/tickets/'+str(item['id']))[0],200)
 def test_05_foreign_read_and_mutations(self):
  _,item,_=self.make();path='/api/tickets/'+str(item['id']);patch={'status':'resolved',**({'version':item['version']} if V2 else {})}
  for method,body in [('GET',None),('PATCH',patch),('DELETE',None)]:self.assertEqual(self.b.request(path,body,method=method)[:2],(404,{'error':'not_found'}))
 def test_06_same_key_same_row(self):
  key,item,payload=self.make(title='  Trim me  ');payload['title']='Trim me';code,replay,_=self.a.request('/api/tickets',payload,headers={'Idempotency-Key':key});self.assertEqual(code,200);self.assertEqual(replay['id'],item['id']);self.assertEqual(replay['title'],'Trim me')
 def test_07_same_key_changed_payload(self):
  key,item,payload=self.make();payload['description']='changed';self.assertEqual(self.a.request('/api/tickets',payload,headers={'Idempotency-Key':key})[:2],(409,{'error':'idempotency_conflict'}));self.assertEqual(self.a.request('/api/tickets/'+str(item['id']))[1]['description'],'Synthetic description')
 def test_08_same_key_other_owner(self):
  key,a,_=self.make();_,b,_=self.make(self.b,key=key);self.assertNotEqual(a['id'],b['id'])
 def test_09_reconcile_scoped(self):
  key,item,_=self.make();self.assertEqual(self.a.request('/api/requests/'+key)[1]['ticket']['id'],item['id']);self.assertEqual(self.b.request('/api/requests/'+key)[0],404)
 def test_10_deleted_receipt_tombstone(self):
  key,item,payload=self.make();self.assertEqual(self.a.request('/api/tickets/'+str(item['id']),method='DELETE')[:2],(204,None));self.assertEqual(self.a.request('/api/tickets',payload,headers={'Idempotency-Key':key})[0],410);self.assertEqual(self.a.request('/api/requests/'+key)[0],410)
 def test_11_bad_json(self):self.assertEqual(self.a.request('/api/tickets',raw=b'{',headers={'Idempotency-Key':'bad-json'})[0],400)
 def test_12_invalid_title_types(self):
  for title in ['', ' '*2, 'x'*81, True, 3, None]:self.assertEqual(self.a.request('/api/tickets',{'title':title,'description':'x'},headers={'Idempotency-Key':'invalid-'+uuid.uuid4().hex})[0],400)
 def test_13_description_type_and_limit(self):
  for d in [True,None,'x'*2001]:self.assertEqual(self.a.request('/api/tickets',{'title':'x','description':d},headers={'Idempotency-Key':'invalid-'+uuid.uuid4().hex})[0],400)
 def test_14_missing_idempotency(self):self.assertEqual(self.a.request('/api/tickets',{'title':'x','description':''})[0],400)
 def test_15_search_and_injection(self):
  title="O'Hare %_ "+uuid.uuid4().hex[:8];_,item,_=self.make(title=title);q=urllib.parse.quote(title);rows=self.a.request('/api/tickets?q='+q)[1]['tickets'];self.assertEqual([x['id'] for x in rows],[item['id']]);self.assertEqual(self.b.request('/api/tickets?q='+q)[1]['tickets'],[])
 def test_16_status_persist_and_invalid(self):
  _,item,_=self.make();path='/api/tickets/'+str(item['id']);data={'status':'working',**({'version':item['version']} if V2 else {})};self.assertEqual(self.a.request(path,data,method='PATCH')[0],200);got=self.a.request(path)[1];self.assertEqual(got['status'],'working');self.assertEqual(self.a.request(path,{'status':'invalid',**({'version':got['version']} if V2 else {})},method='PATCH')[0],400)
 def attach(self,c,item,filename='proof.txt',mime='text/plain',content=b'SYNTHETIC BYTES'):
  return c.request('/api/tickets/'+str(item['id'])+'/attachments',{'filename':filename,'mime':mime,'content_base64':base64.b64encode(content).decode()})
 def test_17_attachment_exact_bytes(self):
  _,item,_=self.make();code,att,_=self.attach(self.a,item);self.assertEqual(code,201);code,data,h=self.a.request('/api/attachments/'+att['id']);self.assertEqual(code,200);self.assertEqual(data,b'SYNTHETIC BYTES');self.assertIn('attachment',h.get('Content-Disposition',''));self.assertEqual(h.get('X-Content-Type-Options'),'nosniff')
 def test_18_attachment_isolation(self):
  _,item,_=self.make();_,att,_=self.attach(self.a,item);self.assertEqual(self.b.request('/api/tickets/'+str(item['id'])+'/attachments')[0],404);self.assertEqual(self.b.request('/api/attachments/'+att['id'])[0],404);self.assertEqual(self.attach(self.b,item)[0],404)
 def test_19_filename_traversal_and_header(self):
  _,item,_=self.make()
  for name in ['../proof.txt','a/b.txt','a\\b.txt','a\r\nb.txt','x.html']:self.assertIn(self.attach(self.a,item,name)[0],[400,413])
 def test_20_attachment_mime_size_magic(self):
  _,item,_=self.make()
  for name,mime,data in [('x.txt','text/plain',b''),('x.txt','text/plain',b'x'*65537),('x.png','image/png',b'not png'),('x.pdf','application/pdf',b'not pdf'),('x.txt','text/html',b'<script>')]:self.assertIn(self.attach(self.a,item,name,mime,data)[0],[400,413])
 def test_21_attachment_delete_ownership(self):
  _,item,_=self.make();_,att,_=self.attach(self.a,item);self.a.request('/api/tickets/'+str(item['id']),method='DELETE');self.assertEqual(self.a.request('/api/attachments/'+att['id'])[0],404)
 def test_22_concurrent_idempotency(self):
  key='race-'+uuid.uuid4().hex;payload={'title':'Concurrent','description':'one outcome'};cookie='; '.join(c.name+'='+c.value for c in self.a.jar)
  def one(_):return Client().request('/api/tickets',payload,headers={'Cookie':cookie,'Idempotency-Key':key})
  with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:results=list(pool.map(one,range(6)))
  self.assertEqual(sum(x[0]==201 for x in results),1);self.assertTrue(all(x[0] in [200,201,503] for x in results));code,receipt,_=self.a.request('/api/requests/'+key);self.assertEqual(code,200);self.created.append((self.a,receipt['ticket']['id']));self.assertEqual(len({x[1]['id'] for x in results if x[0] in [200,201]}),1)
 @unittest.skipUnless(V2,'schema v2 only')
 def test_23_priority_defaults_validation(self):
  _,item,_=self.make();self.assertEqual(item['priority'],'normal');self.assertEqual(item['version'],1)
  for priority in ['urgent',True,None]:self.assertEqual(self.a.request('/api/tickets',{'title':'bad','description':'','priority':priority},headers={'Idempotency-Key':'bad-'+uuid.uuid4().hex})[0],400)
 @unittest.skipUnless(V2,'schema v2 only')
 def test_24_version_conflict_no_mutation(self):
  _,item,_=self.make(priority='high');path='/api/tickets/'+str(item['id']);self.assertEqual(self.a.request(path,{'priority':'low','version':1},method='PATCH')[0],200);self.assertEqual(self.a.request(path,{'status':'resolved','version':1},method='PATCH')[:2],(409,{'error':'version_conflict'}));got=self.a.request(path)[1];self.assertEqual((got['priority'],got['status'],got['version']),('low','open',2))
 @unittest.skipUnless(V2,'schema v2 only')
 def test_25_invalid_version_not_bool(self):
  _,item,_=self.make();path='/api/tickets/'+str(item['id'])
  for data in [{'status':'resolved'},{'version':True,'status':'resolved'},{'version':'1','status':'resolved'}]:self.assertEqual(self.a.request(path,data,method='PATCH')[0],400)
if __name__=='__main__':unittest.main(verbosity=2)
