import base64,hashlib,importlib.util,json,shutil,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
s=importlib.util.spec_from_file_location('flex',Path(__file__).resolve().parents[2]/'backend/flex_video/engine.py');m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
class Tests(unittest.TestCase):
 def test_arbitrary_duration_plan_and_invalid(self):
  self.assertEqual(list(m.chunks(23)),[10,10,3]);self.assertEqual(list(m.chunks(1)),[1])
  for n in [0,-1,True,1.2,'10',2**31]:
   with self.assertRaises(m.Failure):m.validate({'kind':'video.generate','prompt':'p','duration_seconds':n})
  for n in [5,10,20,60,3600]:self.assertEqual(m.validate({'kind':'video.generate','prompt':'p','duration_seconds':n})['duration_seconds'],n)
 def test_owner_isolation_idempotency_and_no_payload_in_sqlite(self):
  with tempfile.TemporaryDirectory() as d:
   e=m.Engine(d,'fixture');v={'kind':'video.generate','prompt':'PRIVATE TEST PROMPT','duration_seconds':23}
   a=e.submit('a'*64,'b'*32,v);b=e.submit('a'*64,'b'*32,v);self.assertEqual(a['job_id'],b['job_id'])
   with self.assertRaises(m.Failure):e.row('c'*64,a['job_id'])
   with self.assertRaises(m.Failure):e.submit('a'*64,'b'*32,{**v,'duration_seconds':24})
   e.db.execute('PRAGMA wal_checkpoint(TRUNCATE)');self.assertNotIn(b'PRIVATE TEST',Path(d,'mechanics.sqlite').read_bytes());e.db.close()
 def test_resource_limit_is_explicit(self):
  with tempfile.TemporaryDirectory() as d:
   e=m.Engine(d,'fixture')
   with patch.object(m.shutil,'disk_usage',return_value=shutil._ntuple_diskusage(100,99,1)):
    with self.assertRaisesRegex(m.Failure,'insufficient_storage'):e.submit('a'*64,'b'*32,{'kind':'video.generate','prompt':'p','duration_seconds':20})
   e.db.close()
 @unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'),'ffmpeg needed')
 def test_real_multisegment_duration_and_cleanup(self):
  with tempfile.TemporaryDirectory() as d:
   class Fake(m.Engine):
    seen=[]
    def remote(self,jid,index,skill,inp,job):
     self.seen.append((index,skill,inp.copy()));return {'url':'/files/fixture.png' if index==-1 else '/files/fixture.mp4'}
    def download_remote(self,result,dest):
     if result['url'].endswith('png'):
      m.command(['ffmpeg','-v','error','-y','-f','lavfi','-i','color=blue:size=512x288','-frames:v','1',str(dest)])
     else:m.command(['ffmpeg','-v','error','-y','-f','lavfi','-i','testsrc2=size=512x288:rate=16','-t','10','-c:v','libx264','-pix_fmt','yuv420p',str(dest)])
   e=Fake(d,'fixture');r=e.submit('a'*64,'b'*32,{'kind':'video.generate','prompt':'p','duration_seconds':11});e.run(r['job_id'])
   end=e.row('a'*64,r['job_id']);self.assertEqual(end['state'],'completed',end)
   self.assertEqual(end['result']['duration_seconds'],11);self.assertEqual(end['progress_seconds'],11)
   self.assertEqual([x[2]['seconds'] for x in e.seen if x[1]=='video.generate'],[10,3])
   self.assertNotEqual(e.seen[1][2]['image_base64'],e.seen[2][2]['image_base64'])
   self.assertEqual(set(p.name for p in (Path(d)/r['job_id']).iterdir()),{'result.mp4','result.json'});e.db.close()
if __name__=='__main__':unittest.main()
