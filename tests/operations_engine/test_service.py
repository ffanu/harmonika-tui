import http.client,json,tempfile,threading,unittest
from pathlib import Path
from backend.operations_engine.engine import Engine
from backend.operations_engine.service import Server,Handler
class ServiceTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name).resolve();self.root.chmod(0o700)
  self.server=Server(('127.0.0.1',0),Handler);self.server.token='fixture-transport-only-'+'x'*32;self.server.slots=threading.BoundedSemaphore(2);self.server.engine=Engine({'state_root':str(self.root/'state'),'principals':{'api-token:1':{'role':'admin','projects':[]},'api-token:2':{'role':'user','projects':[]}},'health_probes':{}},b'k'*32)
  self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
 def tearDown(self):self.server.shutdown();self.server.server_close();self.thread.join();self.temp.cleanup()
 def request(self,body=None,actor='api-token:1',token=True,path='/invoke',method='POST'):
  c=http.client.HTTPConnection('127.0.0.1',self.server.server_port,timeout=3);headers={'Content-Type':'application/json','X-Harmonika-Principal':actor}
  if token:headers['X-Harmonika-Operations-Secret']=self.server.token
  c.request(method,path,body=body,headers=headers);r=c.getresponse();data=json.loads(r.read());status=r.status;c.close();return status,data
 def test_actual_http_auth_and_principal_not_selectable_in_payload(self):
  value=json.dumps({'skill_id':'operations.engine.diagnose','input':{}})
  self.assertEqual(self.request(value,token=False)[0],403);self.assertEqual(self.request(value,actor='api-token:9')[0],403)
  self.assertEqual(self.request(json.dumps({'skill_id':'operations.engine.diagnose','input':{},'actor':'api-token:1'}))[0],400)
  status,data=self.request(value,actor='api-token:2');self.assertEqual(status,200);self.assertEqual(data['evidence_source'],'server_executed');self.assertFalse(data['result']['healthy'])
 def test_unknown_keys_json_and_private_scope_fail_closed(self):
  self.assertEqual(self.request('{"skill_id":"operations.engine.diagnose","input":{},"input":{}}')[0],400)
  self.assertEqual(self.request(json.dumps({'skill_id':'operations.security.scan','input':{'project':'private','path':'.'}}),actor='api-token:2')[0],403)
  self.assertEqual(self.request(json.dumps({'skill_id':'operations.license.usage','input':{'scope':'all'}}),actor='api-token:2')[0],403)
 def test_non_ascii_transport_secret_is_uniform_forbidden(self):
  connection=http.client.HTTPConnection('127.0.0.1',self.server.server_port,timeout=3)
  connection.request('GET','/capabilities',headers={'X-Harmonika-Operations-Secret':'é','X-Harmonika-Principal':'api-token:1'})
  response=connection.getresponse();self.assertEqual(response.status,403);self.assertEqual(json.loads(response.read())['error']['code'],'access_denied');connection.close()
 def test_capacity_does_not_dispatch_or_replay(self):
  self.server.slots.acquire();self.server.slots.acquire()
  try:self.assertEqual(self.request(json.dumps({'skill_id':'operations.engine.diagnose','input':{}}))[0],429)
  finally:self.server.slots.release();self.server.slots.release()
  self.assertEqual(self.request(json.dumps({'skill_id':'operations.engine.diagnose','input':{}}))[0],200)
if __name__=='__main__':unittest.main()
