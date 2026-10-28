import unittest,importlib.util,tempfile,hashlib,hmac,json
from pathlib import Path
from unittest.mock import patch
spec=importlib.util.spec_from_file_location('video_facade',Path(__file__).resolve().parents[2]/'backend/video_routing/facade.py');f=importlib.util.module_from_spec(spec);spec.loader.exec_module(f)
class Tests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.old=f.REPLAY;f.REPLAY=Path(self.tmp.name)/'r.sqlite3';self.addCleanup(setattr,f,'REPLAY',self.old);self.cfg={'clients':{'test':{'secret':'ab'*32}}};self.data={'operation':'video_capabilities','owner_context':'dev_'+'a'*32};self.proof={'client':'test','issued':100,'nonce':'a'*32};self.proof['signature']=hmac.new(bytes.fromhex('ab'*32),f.canonical({'input':self.data,**self.proof}),hashlib.sha256).hexdigest();self.signed={**self.data,'_video_auth':self.proof}
 def test_auth_replay(self):
  data,owner=f.authorize(self.signed,self.cfg,100);self.assertEqual(data,self.data);self.assertEqual(len(owner),64)
  with self.assertRaisesRegex(f.Failure,'replayed'):f.authorize(self.signed,self.cfg,100)
 def test_forgery(self):
  with self.assertRaises(f.Failure):f.authorize({**self.signed,'owner_context':'dev_'+'b'*32},self.cfg,100)
 def test_expired(self):
  with self.assertRaisesRegex(f.Failure,'expired'):f.authorize(self.signed,self.cfg,300)
 def test_unsigned(self):
  with self.assertRaises(f.Failure):f.authorize(self.data,self.cfg,100)
 def test_duration(self):
  with self.assertRaisesRegex(f.Failure,'unsupported'):f.execute({'operation':'generate_video','owner_context':'x','prompt':'p','duration_seconds':60,'request_id':'a'*32},'owner')
 def test_unknown_fields(self):
  with self.assertRaises(f.Failure):f.execute({**self.data,'token':'secret'},'owner')
 def test_uncertain_get_only(self):
  calls=[]
  def fake(method,*args,**kwargs):
   calls.append(method);raise f.Failure('video_submission_uncertain' if method=='POST' else 'not_found')
  with patch.object(f,'call',fake):result=f.execute({'operation':'generate_video','prompt':'p','request_id':'a'*32},'owner')
  self.assertEqual(calls,['POST','GET']);self.assertFalse(result['retry_submission'])
 def test_checksum(self):
  row={'id':'a'*32,'state':'completed','result':{'sha256':'b'*64}}
  with patch.object(f,'call',side_effect=[row,b'bad']):
   with self.assertRaisesRegex(f.Failure,'checksum'):f.execute({'operation':'download_video','job_id':'a'*32},'owner')
 def test_reference_is_visually_valid_and_forwarded(self):
  import io,base64
  from PIL import Image
  b=io.BytesIO();Image.new('RGB',(64,64),'blue').save(b,format='PNG');encoded=base64.b64encode(b.getvalue()).decode()
  with patch.object(f,'call',return_value={'id':'a'*32,'state':'queued','error_type':None,'latency':None,'result':None}) as c:
   r=f.execute({'operation':'animate_video','prompt':'gentle camera motion','request_id':'b'*32,'reference_image_base64':encoded},'owner')
   self.assertEqual(r['state'],'queued');self.assertEqual(c.call_args.args[3]['kind'],'video.animate_image');self.assertEqual(c.call_args.args[3]['image_base64'],encoded)
 def test_invalid_reference_and_duration_never_submit(self):
  for params in [{'reference_image_base64':'broken'},{'reference_image_base64':'a'*200,'duration_seconds':10}]:
   with patch.object(f,'call') as c:
    with self.assertRaises(f.Failure):f.execute({'operation':'animate_video','prompt':'p','request_id':'b'*32,**params},'owner')
    c.assert_not_called()
if __name__=='__main__':unittest.main()

