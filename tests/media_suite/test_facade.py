import json,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'backend/media_suite'))
import rpc_facade as f
class Facade(unittest.TestCase):
 def test_research_tts_cannot_escape_to_public(self):
  with patch.object(f,'request') as r:
   v=f.safe_dispatch({'operation':'synthesize_speech','text':'hello'});self.assertEqual(v['error']['code'],'license_restricted');r.assert_not_called()
 def test_unknown_fields_not_dispatched(self):
  with patch.object(f,'request') as r:
   v=f.safe_dispatch({'operation':'upscale_image','image_base64':'YQ==','endpoint':'http://evil'});self.assertFalse(v['ok']);r.assert_not_called()
 def test_isolated_job_ids_and_no_replay_leak(self):
  with tempfile.TemporaryDirectory() as d:
   Path(d,'config.json').write_text('{}');calls=[]
   def req(port,path,body=None,idem=None):
    calls.append((path,idem))
    if path=='/health':return {'capabilities':{'audio.transcribe':{'ready':True}}}
    if path=='/v1/jobs':return {'job_id':'a'*32}
    return {'state':'completed','result':{'transcript':'fixture','language':'id'}}
   with patch.object(f,'ROOT',Path(d)),patch.object(f,'request',side_effect=req):
    a=f.safe_dispatch({'operation':'transcribe_audio','audio_base64':'YQ=='});b=f.safe_dispatch({'operation':'transcribe_audio','audio_base64':'YQ=='})
   self.assertTrue(a['ok']);self.assertNotIn('job_id',a);keys=[x[1] for x in calls if x[0]=='/v1/jobs'];self.assertNotEqual(keys[0],keys[1])
 def test_ambiguous_post_never_retried(self):
  with tempfile.TemporaryDirectory() as d:
   Path(d,'config.json').write_text('{}')
   with patch.object(f,'ROOT',Path(d)),patch.object(f,'request',side_effect=[{'capabilities':{'audio.transcribe':{'ready':True}}},f.RPCError('media_submission_uncertain')]) as r:
    v=f.safe_dispatch({'operation':'transcribe_audio','audio_base64':'YQ=='});self.assertEqual(r.call_count,2);self.assertFalse(v['retry_safe']);self.assertEqual(v['operation_state'],'uncertain')
 def test_errors_are_redacted(self):
  with patch.object(f,'dispatch',side_effect=Exception('private password audio contents')):
   v=f.safe_dispatch({});self.assertNotIn('password',json.dumps(v));self.assertFalse(v['retry_safe'])

 def test_post_500_is_uncertain(self):
  import urllib.error
  with tempfile.TemporaryDirectory() as d:
   Path(d,'service.token').write_text('x'*48)
   with patch.object(f,'ROOT',Path(d)),patch('urllib.request.build_opener') as builder:
    builder.return_value.open.side_effect=urllib.error.HTTPError('http://127.0.0.1:18230/v1/jobs',500,'error',{},None)
    with self.assertRaisesRegex(f.RPCError,'media_submission_uncertain'):f.request(18230,'/v1/jobs',{'fixture':True})
