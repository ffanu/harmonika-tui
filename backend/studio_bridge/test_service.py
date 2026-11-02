import unittest,tempfile,hashlib,base64,json
from pathlib import Path
from service import Engine,Failure
class Tests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.e=Engine(self.tmp.name,{'owner':'owner','api_key':'fake','bridge_token':'fake'},False)
 def rpc(self,**kw):return self.e.rpc({'owner':'owner',**kw})
 def test_owner(self):
  with self.assertRaisesRegex(Failure,'owner'):self.e.rpc({'owner':'other','action':'assets'})
 def test_idempotency_and_conflict(self):
  a=self.rpc(action='render',request_id='abc',params={'asset_id':'ast_test','keep':[{'in':0,'out':1}]});b=self.rpc(action='render',request_id='abc',params={'asset_id':'ast_test','keep':[{'in':0,'out':1}]});self.assertEqual(a['job_id'],b['job_id'])
  with self.assertRaisesRegex(Failure,'conflict'):self.rpc(action='direct',request_id='abc',asset_id='ast_test',params={})
  self.assertEqual(self.rpc(action='job',request_id='abc')['job_id'],a['job_id'])
 def test_cross_owner_jobs(self):
  j=self.rpc(action='direct',request_id='a',asset_id='ast_a',params={})
  with self.assertRaisesRegex(Failure,'not_found'):self.e.row('someone',j['job_id'])
 def test_invalid_params(self):
  for p in [{'asset_id':'ast_a','speed':5},{'asset_id':'ast_a','keep':[{'in':4,'out':1}]},{'asset_id':'ast_a','evil':True},{'asset_id':'../../etc/passwd'}]:
   with self.assertRaises(Failure):self.rpc(action='render',request_id='a',params=p)
 def test_chunk_retry_finish(self):
  raw=b'\x00\x00\x00\x18ftypisom'+b'x'*500
  u=self.rpc(action='upload_begin',request_id='u',filename='x.mp4',bytes=len(raw),sha256=hashlib.sha256(raw).hexdigest())
  chunk={'action':'upload_chunk','upload_id':u['upload_id'],'offset':0,'data':base64.b64encode(raw).decode()}
  a=self.rpc(**chunk);self.assertEqual(a,self.rpc(**chunk));self.assertEqual(a['offset'],len(raw))
  j=self.rpc(action='upload_finish',upload_id=u['upload_id']);self.assertEqual(j['job_id'],self.rpc(action='upload_finish',upload_id=u['upload_id'])['job_id'])
 def test_bad_chunk_and_checksum(self):
  u=self.rpc(action='upload_begin',request_id='u',filename='x.mp4',bytes=12,sha256='0'*64)
  with self.assertRaises(Failure):self.rpc(action='upload_chunk',upload_id=u['upload_id'],offset=0,data='?')
  with self.assertRaises(Failure):self.rpc(action='upload_finish',upload_id=u['upload_id'])
 def test_crash_is_unknown(self):
  j=self.rpc(action='direct',request_id='a',asset_id='ast_a',params={})
  with self.e.db() as c:c.execute("UPDATE jobs SET state='dispatching' WHERE id=?",(j['job_id'],))
  e=Engine(self.tmp.name,self.e.cfg,False);self.assertEqual(e.row('owner',j['job_id'])['state'],'unknown')
 def test_completed_upload(self):
  j=self.e.enqueue('owner','a','upload',{'upload_id':'up_'+'a'*32});calls=[]
  def req(*a,**k):calls.append(a);return {'asset_id':'ast_test'}
  self.e.request=req;self.e.work(self.e.row('owner',j['job_id']));self.assertEqual(len(calls),1);self.assertEqual(self.e.row('owner',j['job_id'])['state'],'completed')
 def test_remote_poll_no_second_post(self):
  j=self.rpc(action='render',request_id='a',params={'asset_id':'ast_test'});calls=[]
  def req(method,path,*a,**kw):
   calls.append(method)
   return {'job_id':'job_test'} if method=='POST' else {'state':'completed','result':{'timeline':[]}}
  self.e.request=req;self.e.work(self.e.row('owner',j['job_id']));self.assertEqual(calls,['POST','GET'])
 def test_remote_artifact_url_not_trusted(self):
  j=self.rpc(action='direct',request_id='a',asset_id='ast_a',params={})
  for url in ['https://evil.test/file','/exports/../secret','/exports/video.mp4?key=secret','/etc/passwd']:
   with self.assertRaisesRegex(Failure,'artifact_url'):self.e.finish(self.e.row('owner',j['job_id']),{'url':url})
 def test_stored_state_has_no_api_key(self):
  self.rpc(action='direct',request_id='a',asset_id='ast_a',params={})
  with self.e.db() as c:dump=''.join(c.iterdump())
  self.assertNotIn('api_key',dump);self.assertNotIn('bridge_token',dump)
 def test_known_remote_poll_can_resume_without_post(self):
  j=self.rpc(action='render',request_id='a',params={'asset_id':'ast_a'})
  with self.e.db() as c:c.execute("UPDATE jobs SET state='unknown',remote='job_remote',error='studio_poll_timeout' WHERE id=?",(j['job_id'],))
  self.assertEqual(self.rpc(action='job',job_id=j['job_id'])['state'],'polling')
 def test_unknown_post_never_requeues(self):
  j=self.rpc(action='direct',request_id='a',asset_id='ast_a',params={})
  with self.e.db() as c:c.execute("UPDATE jobs SET state='unknown',error='studio_submission_uncertain' WHERE id=?",(j['job_id'],))
  r=self.rpc(action='direct',request_id='a',asset_id='ast_a',params={});self.assertEqual(r['state'],'unknown');self.assertTrue(r['requires_reconciliation'])
 def test_cloud_generator_is_not_implicitly_enabled(self):
  with self.assertRaisesRegex(Failure,'existing_video'):self.rpc(action='generate',request_id='a',params={'prompt':'example'})
 def test_artifact_fetch_resume_only_gets(self):
  j=self.rpc(action='render',request_id='a',params={'asset_id':'ast_a'})
  with self.e.db() as c:c.execute("UPDATE jobs SET state='fetching',result=? WHERE id=?",(json.dumps({'result':{'url':'/exports/example.mp4'}}),j['job_id']))
  calls=[]
  def req(method,path,*a,**kw):calls.append(method);return {'bytes':16,'sha256':'0'*64,'mime_type':'video/mp4'}
  self.e.request=req;self.e.work(self.e.row('owner',j['job_id']));self.assertEqual(calls,['GET']);self.assertEqual(self.e.row('owner',j['job_id'])['state'],'completed')
if __name__=='__main__':unittest.main()
