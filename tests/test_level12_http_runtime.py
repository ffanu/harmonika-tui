import hashlib,http.client,importlib.util,json,pathlib,secrets,socket,sqlite3,sys,tempfile,threading,unittest
BASE=pathlib.Path(__file__).resolve().parents[1]/'scripts/level12-lab'
def load(name,file):
 s=importlib.util.spec_from_file_location(name,file);m=importlib.util.module_from_spec(s);sys.modules[name]=m;s.loader.exec_module(m);return m
Security=load('security',BASE/'security.py');R=load('level12_web_runtime',BASE/'web_runtime.py')
class Handler(R.RuntimeHandler):
 def route(self,method,path,query,data,principal):
  if path=='/api/demo':return self.reply(200,{'user':principal,'data':data})
  return super().route(method,path,query,data,principal)
class RuntimeTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.root=pathlib.Path(self.tmp.name);self.password=secrets.token_urlsafe(24);self.db=self.root/'crm.sqlite';salt=secrets.token_hex(16);cfg=self.root/'auth.json';cfg.write_text(json.dumps({'users':{'admin':{'role':'admin','salt':salt,'hash':hashlib.pbkdf2_hmac('sha256',self.password.encode(),bytes.fromhex(salt),180000).hex()}}}));self.cfg=cfg
  with sqlite3.connect(self.db) as c:c.execute('PRAGMA journal_mode=WAL')
  (self.root/'static').mkdir();(self.root/'static/index.html').write_text('<!doctype html><h1>HARMONIKA</h1>');self.origin='https://test.asrafijm.com';self.server=R.create_server(Handler,object(),self.db,cfg,self.origin,'test-v1',self.root);self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
 def tearDown(self):self.server.shutdown();self.server.server_close();self.thread.join(3);self.tmp.cleanup()
 def request(self,method,path,body=None,headers=None,raw=None):
  conn=http.client.HTTPConnection('127.0.0.1',self.server.server_port,timeout=4);data=raw if raw is not None else json.dumps(body).encode() if body is not None else None;conn.request(method,path,body=data,headers={'Origin':self.origin,'Content-Type':'application/json',**(headers or {})});r=conn.getresponse();blob=r.read();result=(r.status,dict(r.getheaders()),json.loads(blob) if r.getheader('Content-Type','').startswith('application/json') else blob);conn.close();return result
 def login(self):
  status,h,v=self.request('POST','/api/login',{'username':'admin','password':self.password});self.assertEqual(status,200);return {'Cookie':h['Set-Cookie'].split(';')[0],'X-CSRF-Token':v['csrf']}
 def test_real_login_scope_csrf_and_logout(self):
  self.assertEqual(self.request('GET','/api/demo')[0],401);h=self.login();self.assertEqual(self.request('GET','/api/demo',headers=h)[2]['user']['role'],'admin');self.assertEqual(self.request('POST','/api/demo',{},headers={**h,'X-CSRF-Token':'bad'})[0],403);self.assertEqual(self.request('POST','/api/demo',{},headers=h)[0],200);self.assertEqual(self.request('GET','/api/demo',headers={**h,'X-Role':'admin'})[0],403);self.assertEqual(self.request('POST','/api/logout',{},headers=h)[0],200);self.assertEqual(self.request('GET','/api/demo',headers=h)[0],401)
 def test_login_origin_and_secure_cookie(self):
  self.assertEqual(self.request('POST','/api/login',{},headers={'Origin':'https://evil.example'})[0],403);status,h,v=self.request('POST','/api/login',{'username':'admin','password':self.password});self.assertEqual(status,200)
  for flag in ['Secure','HttpOnly','SameSite=Strict']:self.assertIn(flag,h['Set-Cookie'])
 def test_payload_and_pipeline(self):
  self.assertEqual(self.request('POST','/api/demo',{'huge':'x'*33000})[0],401);h=self.login();self.assertEqual(self.request('POST','/api/demo',{'huge':'x'*33000},h)[0],413);self.assertEqual(self.request('POST','/api/demo',[],h)[0],422);self.assertEqual(self.request('POST','/api/logout',{'huge':'x'*33000},h)[0],413);self.assertEqual(self.request('GET','/api/demo',headers=h)[0],200);self.assertEqual(self.request('PUT','/api/demo',{},h)[0],405);self.assertEqual(self.request('POST','/api/demo',headers=h,raw=b'{broken')[0],422);self.assertEqual(self.request('GET','/api/demo?'+('&'.join('a'+str(n)+'=1' for n in range(20))),headers=h)[0],422)
 def test_real_writer_lock_health_ready_recovery(self):
  c=sqlite3.connect(self.db);c.execute('BEGIN IMMEDIATE')
  try:self.assertEqual(self.request('GET','/healthz')[0],200);self.assertEqual(self.request('GET','/readyz')[0],503)
  finally:c.rollback();c.close()
  self.assertEqual(self.request('GET','/readyz')[0],200)
 def test_static_allowlist_and_headers(self):
  status,h,body=self.request('GET','/');self.assertEqual(status,200);self.assertIn(b'HARMONIKA',body);self.assertEqual(h['X-Content-Type-Options'],'nosniff');self.assertEqual(h['X-Frame-Options'],'DENY');self.assertIn("script-src 'self'",h['Content-Security-Policy'])
  for path in ['/app.py','/security.py','/auth.json','/crm.sqlite','/static/../auth.json','/static/%2e%2e/auth.json']:self.assertEqual(self.request('GET',path)[0],404)
 def test_bounded_threads_return_structured_backpressure(self):
  original=Handler.setup;seen=[];guard=threading.Lock();ready=threading.Event()
  def observed(handler):
   original(handler)
   with guard:
    seen.append(True)
    if len(seen)==8:ready.set()
  Handler.setup=observed;sockets=[]
  try:
   for _ in range(8):sockets.append(socket.create_connection(self.server.server_address,timeout=2))
   self.assertTrue(ready.wait(3));status,headers,body=self.request('GET','/healthz');self.assertEqual(status,503);self.assertEqual(body,{'error':'http_busy'})
  finally:
   Handler.setup=original
   for s in sockets:s.close()
 def test_owned_port_collision_fallback(self):
  holder=socket.socket();holder.bind(('127.0.0.1',0));holder.listen();port=holder.getsockname()[1]
  try:
   alternate=R.create_server(Handler,object(),self.db,self.cfg,self.origin,'v1',self.root,port);self.assertNotIn(alternate.server_port,[3000,port]);self.assertEqual(alternate.server_address[0],'127.0.0.1');alternate.server_close()
  finally:holder.close()
if __name__=='__main__':unittest.main()
