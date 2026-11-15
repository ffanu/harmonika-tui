import json,threading,tempfile,unittest,urllib.request,urllib.error
from pathlib import Path
import app
class Public(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.s=app.make_server(str(Path(self.tmp.name)/'app.sqlite'));self.t=threading.Thread(target=self.s.serve_forever);self.t.start();self.url='http://127.0.0.1:'+str(self.s.server_address[1])
 def tearDown(self):self.s.shutdown();self.t.join();self.s.server_close();self.tmp.cleanup()
 def req(self,path,body=None):
  r=urllib.request.Request(self.url+path,data=json.dumps(body).encode() if body is not None else None,headers={'Content-Type':'application/json'})
  try:v=urllib.request.urlopen(r,timeout=5)
  except urllib.error.HTTPError as e:v=e
  with v:return v.status,json.load(v)
 def test_ready_and_schema(self):
  status,value=self.req('/readyz');self.assertEqual(status,200);self.assertTrue(value['ready']);self.assertGreaterEqual(value['schema_version'],1)
 def test_unauthenticated_cannot_list(self):self.assertEqual(self.req('/api/tickets')[0],401)
if __name__=='__main__':unittest.main()
