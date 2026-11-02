import importlib.util,unittest
from pathlib import Path
from unittest.mock import patch
s=importlib.util.spec_from_file_location('proxy',Path(__file__).resolve().parents[2]/'backend/flex_video/proxy.py');m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
class Tests(unittest.TestCase):
 def test_new_duration_is_preserved_for_image(self):
  with patch.object(m,'call',return_value={'ok':True,'state':'queued'}) as call:
   out=m.route({'operation':'animate_video','duration_seconds':23,'reference_image_base64':'fixture','request_id':'b'*32,'prompt':'p'},'a'*64)
   self.assertEqual(call.call_args.kwargs['input']['duration_seconds'],23);self.assertFalse(out['retry_submission'])
 def test_old_profiles_do_not_change(self):
  with patch.object(m,'call',side_effect=AssertionError('new service unnecessary')):
   for op,n in [('animate_video',1),('generate_video',1),('generate_video',10)]:self.assertIsNone(m.route({'operation':op,'duration_seconds':n},'owner'))
 def test_missing_old_job_falls_through_even_if_prefix_matches(self):
  with patch.object(m,'call',return_value={'ok':False,'error':{'code':'not_found'}}):self.assertIsNone(m.route({'operation':'download_video','job_id':'d04a'+'b'*28},'owner'))
 def test_offline_new_service_does_not_break_old_request_lookup(self):
  with patch.object(m,'call',return_value={'ok':False,'error':{'code':'flex_video_unavailable'}}):self.assertIsNone(m.route({'operation':'video_request_status','request_id':'a'*32},'owner'))
if __name__=='__main__':unittest.main()
