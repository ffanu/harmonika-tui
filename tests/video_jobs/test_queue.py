import importlib.util,json,sqlite3,tempfile,threading,time,unittest
from pathlib import Path
spec=importlib.util.spec_from_file_location('video_service',Path(__file__).resolve().parents[2]/'backend/video_jobs/service.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
class QueueTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.store=m.Store(self.tmp.name,self.tmp.name,'x'*64)
 def tearDown(self):
  self.store.db.close();self.tmp.cleanup()
 def test_grid(self):
  self.assertEqual(m._renderer.frame_count(10),243);self.assertEqual(m._renderer.frame_count(5),124)
 def test_invalid(self):
  for data in ({},{'prompt':'x','duration_seconds':True},{'prompt':'x','duration_seconds':11},{'prompt':'a'*8001},{'prompt':'x','workflow':{}}):
   with self.assertRaises(ValueError):m.validate(data)
 def test_idempotency_and_ownership(self):
  a=self.store.submit('a'*64,'1'*32,{'prompt':'private sentinel','duration_seconds':10})
  b=self.store.submit('a'*64,'1'*32,{'prompt':'private sentinel','duration_seconds':10})
  self.assertEqual(a['id'],b['id']);self.assertIsNone(self.store.row('b'*64,a['id']))
  with self.assertRaisesRegex(ValueError,'idempotency_conflict'):self.store.submit('a'*64,'1'*32,{'prompt':'other'})
  self.assertNotIn('private sentinel',(self.store.folder/'jobs.sqlite3').read_bytes().decode(errors='ignore'))
  self.assertNotIn('prompt',self.store.public(a));self.assertNotIn('owner',self.store.public(a))
 def test_one_active_per_owner(self):
  self.store.submit('a'*64,'1'*32,{'prompt':'x'})
  with self.assertRaisesRegex(ValueError,'queue_full'):self.store.submit('a'*64,'2'*32,{'prompt':'x'})
 def test_restart_unknown(self):
  a=self.store.submit('a'*64,'1'*32,{'prompt':'x'})
  other=m.Store(self.tmp.name,self.tmp.name,'x'*64)
  self.assertTrue(other.halted);self.assertEqual(other.row('a'*64,a['id'])['state'],'unknown');other.db.close()
 def test_public_result(self):
  row=self.store.submit('a'*64,'1'*32,{'prompt':'x'})
  public=self.store.public(row)
  self.assertEqual(public['state'],'queued');self.assertIsNone(public['result'])
if __name__=='__main__':unittest.main()

class HttpTests(unittest.TestCase):
 def test_real_http_auth_idempotency_and_foreign_owner(self):
  import urllib.request,urllib.error,json
  from http.server import ThreadingHTTPServer
  with tempfile.TemporaryDirectory() as folder:
   store=m.Store(folder,folder,'x'*64)
   server=ThreadingHTTPServer(('127.0.0.1',0),m.handler(store));thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
   base='http://127.0.0.1:'+str(server.server_port)
   def call(path,owner='a'*64,auth='Bearer '+'x'*64,body=None,idem='1'*32):
    req=urllib.request.Request(base+path,headers={'Authorization':auth,'X-Resource-Owner':owner,'Idempotency-Key':idem,'Content-Type':'application/json'},data=json.dumps(body).encode() if body is not None else None)
    try:
     with urllib.request.urlopen(req,timeout=2) as r:return r.status,json.load(r)
    except urllib.error.HTTPError as e:return e.code,json.load(e)
   try:
    self.assertEqual(call('/health',auth='')[0],401)
    self.assertEqual(call('/health',auth='Bearer é')[0],401)
    code,job=call('/jobs',body={'prompt':'test only'})
    self.assertEqual(code,202)
    self.assertEqual(call('/requests/'+'1'*32)[1]['id'],job['id'])
    self.assertEqual(call('/requests/'+'1'*32,owner='b'*64)[0],404)
    self.assertEqual(call('/jobs/'+job['id']+'/file',owner='b'*64)[0],404)
    self.assertEqual(call('/jobs',body={'prompt':'other'})[0],409)
    self.assertEqual(call('/jobs',body={'prompt':'test only'})[1]['id'],job['id'])
   finally:server.shutdown();server.server_close();thread.join();store.db.close()
 def test_retention(self):
  with tempfile.TemporaryDirectory() as folder:
   store=m.Store(folder,folder,'x'*64);job=store.submit('a'*64,'1'*32,{'prompt':'test'})
   p=Path(folder)/(job['id']+'.mp4');p.write_bytes(b'expired synthetic fixture')
   store.db.execute("UPDATE jobs SET state='completed',updated=? WHERE id=?",(time.time()-8*86400,job['id']));store.db.commit();store.prune()
   self.assertFalse(p.exists());self.assertIsNone(store.row('a'*64,job['id']));store.db.close()
