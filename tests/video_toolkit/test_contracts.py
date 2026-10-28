import importlib.util,json,hashlib,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
BASE=Path(__file__).resolve().parents[2]/'backend/video_toolkit'
def module(name):
 spec=importlib.util.spec_from_file_location(name,BASE/(name+'.py'));m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m
m=module('broker');client=module('client')
class Tests(unittest.TestCase):
 def test_valid_generation(self):self.assertEqual(m.validate({'kind':'video.generate','prompt':'router','duration_seconds':1})['kind'],'video.generate')
 def test_bad_profiles(self):
  for data in [{},{'kind':'video.generate','prompt':''},{'kind':'video.generate','prompt':'x','duration_seconds':True},{'kind':'video.generate','prompt':'x','duration_seconds':10},{'kind':'video.generate','prompt':'a'*8001},{'kind':'video.generate','prompt':'x','url':'http://localhost'}]:
   with self.assertRaises(ValueError):m.validate(data)
 def test_research_license_hard_block(self):
  with self.assertRaisesRegex(ValueError,'license_restricted'):m.validate({'kind':'audio.soundtrack','prompt':'sound'})
  with self.assertRaises(ValueError):m.validate({'kind':'audio.soundtrack','prompt':'sound','research':True})
 def test_image_required_and_bounded(self):
  for image in [None,'invalid!','YQ==','x'*(13*1024*1024)]:
   with self.assertRaises(ValueError):m.validate({'kind':'video.animate_image','prompt':'move','image_base64':image})
 def test_image_signature_rejected_before_queue(self):
  import base64
  with self.assertRaisesRegex(ValueError,'invalid_image'):
   m.validate({'kind':'video.animate_image','prompt':'move','image_base64':base64.b64encode(b'not a picture'*100).decode()})
 def test_reference_not_silently_ignored(self):
  with self.assertRaisesRegex(ValueError,'unsupported_reference'):m.validate({'kind':'video.generate','prompt':'x','image_base64':'a'})
 def test_registry_truthful(self):
  with patch.object(m,'read',return_value={}):
   caps=m.capabilities()
   for item in caps['engines'].values():self.assertFalse(item['operator_ready']);self.assertFalse(item['public_route_enabled'])
  with patch.object(m,'read',side_effect=lambda n:{'phase':'verified'} if 'qualification' in n else {'phase':'dependencies_installed'} if 'install' in n else {'models_sha256_verified':True}):
   caps=m.capabilities()['engines'];self.assertTrue(caps['framepack']['operator_ready']);self.assertFalse(caps['mmaudio']['operator_ready']);self.assertTrue(caps['mmaudio']['research_cli_ready'])
 def test_database_and_replay_privacy(self):
  with tempfile.TemporaryDirectory() as folder,patch.object(m,'CONTROL',Path(folder)),patch.object(m,'capabilities',return_value={'engines':{'wangp':{'operator_ready':True}}}):
   broker=m.Broker();payload={'kind':'video.generate','prompt':'private sentinel'}
   a=broker.submit('a'*64,'1'*32,payload);b=broker.submit('a'*64,'1'*32,payload)
   self.assertEqual(a['id'],b['id']);self.assertIsNone(broker.row('b'*64,a['id']))
   self.assertNotIn('prompt',broker.public(a));self.assertNotIn(b'private sentinel',(Path(folder)/'broker.sqlite3-wal').read_bytes())
   with self.assertRaisesRegex(ValueError,'idempotency_conflict'):broker.submit('a'*64,'1'*32,{'kind':'video.generate','prompt':'other'})
   broker.db.close()
 def test_unknown_restart_holds_gpu_queue(self):
  with tempfile.TemporaryDirectory() as folder,patch.object(m,'CONTROL',Path(folder)),patch.object(m,'capabilities',return_value={'engines':{'wangp':{'operator_ready':True}}}):
   first=m.Broker();job=first.submit('a'*64,'1'*32,{'kind':'video.generate','prompt':'fixture'})
   second=m.Broker();self.assertEqual(second.row('a'*64,job['id'])['state'],'unknown')
   with self.assertRaisesRegex(ValueError,'engine_recovery_required'):second.submit('b'*64,'2'*32,{'kind':'video.generate','prompt':'fixture'})
   first.db.close();second.db.close()
 def test_ambiguous_client_submit_get_only_recovery(self):
  c=client.Client.__new__(client.Client)
  with patch.object(c,'call',side_effect=[{'submission_uncertain':True},{'id':'a'*32,'state':'queued'}]) as call:
   out=c.submit('video.generate','fixture',request_id='1'*32)
   self.assertEqual(out['id'],'a'*32);self.assertEqual(out['request_id'],'1'*32);self.assertEqual([x.args[0] for x in call.call_args_list],['POST','GET'])
 def test_artifact_integrity_and_no_overwrite(self):
  import io
  from unittest.mock import MagicMock
  raw=b'synthetic-mp4';digest=hashlib.sha256(raw).hexdigest()
  class Response(io.BytesIO):headers={'Content-Length':str(len(raw)),'Content-Type':'video/mp4'}
  c=client.Client.__new__(client.Client);c.token='fixture';c.owner='a'*64
  with tempfile.TemporaryDirectory() as folder,patch.object(c,'job',return_value={'state':'completed','result':{'sha256':digest}}),patch.object(client.urllib.request,'build_opener') as opener:
   opener.return_value.open.return_value=Response(raw);target=Path(folder)/'test.mp4';out=c.download('1'*32,target)
   self.assertEqual(out['status'],'completed');self.assertEqual(target.read_bytes(),raw)
   self.assertEqual(c.download('1'*32,target)['error'],'artifact_destination_exists');self.assertEqual(list(Path(folder).glob('*.partial')),[])
 def test_artifact_bad_hash_removes_only_own_partial(self):
  import io
  class Response(io.BytesIO):headers={'Content-Length':'3','Content-Type':'video/mp4'}
  c=client.Client.__new__(client.Client);c.token='fixture';c.owner='a'*64
  with tempfile.TemporaryDirectory() as folder,patch.object(c,'job',return_value={'state':'completed','result':{'sha256':'0'*64}}),patch.object(client.urllib.request,'build_opener') as opener:
   opener.return_value.open.return_value=Response(b'bad');target=Path(folder)/'test.mp4';self.assertEqual(c.download('1'*32,target)['error'],'artifact_unavailable');self.assertEqual(list(Path(folder).iterdir()),[])
 def test_duplicate_broker_never_mutates_live_database(self):
  import types
  from unittest.mock import Mock
  win=types.SimpleNamespace(LK_NBLCK=1,locking=Mock(side_effect=OSError('already locked')))
  with tempfile.TemporaryDirectory() as folder,patch.object(m,'CONTROL',Path(folder)),patch.dict('sys.modules',{'msvcrt':win}),patch.object(m,'Broker') as constructor:
   m.main();constructor.assert_not_called();self.assertFalse((Path(folder)/'broker.sqlite3').exists())
 def test_runtime_errors_never_echo_private_text(self):
  import os
  with patch.dict(os.environ):frame=module('framepack_runner')
  self.assertEqual(frame.mechanical_error_code(AssertionError('gpu_busy')),'gpu_busy')
  self.assertEqual(frame.mechanical_error_code(RuntimeError('private customer sentinel')),'inference_failed')
  oom=type('OutOfMemoryError',(RuntimeError,),{})('private customer sentinel')
  self.assertEqual(frame.mechanical_error_code(oom),'insufficient_vram')
 def test_busy_gpu_returns_structured_error_and_worker_survives(self):
  import types
  from unittest.mock import Mock
  win=types.SimpleNamespace(LK_NBLCK=1,locking=Mock(side_effect=OSError('busy')))
  with tempfile.TemporaryDirectory() as folder,patch.object(m,'CONTROL',Path(folder)),patch.object(m,'JOBS',Path(folder)/'jobs'),patch.object(m,'capabilities',return_value={'engines':{'wangp':{'operator_ready':True}}}),patch.dict('sys.modules',{'msvcrt':win}):
   broker=m.Broker();payload={'kind':'video.generate','prompt':'fixture'};job=broker.submit('a'*64,'1'*32,payload)
   with patch.object(broker.pending,'get',side_effect=[(job['id'],payload),StopIteration]):
    with self.assertRaises(StopIteration):broker.worker()
   row=broker.row('a'*64,job['id']);self.assertEqual(row['state'],'failed');self.assertEqual(row['error_type'],'gpu_busy');self.assertEqual(broker.pending.unfinished_tasks,0);broker.db.close()
 def test_source_and_model_pins(self):
  for row in json.loads((BASE/'sources.json').read_text()).values():self.assertEqual(len(row['revision']),40);self.assertEqual(len(row['archive_sha256']),64)
  rows=json.loads((BASE/'models.json').read_text());self.assertEqual(len(rows),55)
  for row in rows:self.assertEqual(len(row['sha256']),64);self.assertNotIn('..',Path(row['relative']).parts);self.assertIn(row['revision'],row['url'])
 def test_framepack_exact_section_overshoot(self):
  import math
  for seconds in range(1,11):
   sections=math.ceil((seconds*30-1)/36);self.assertGreaterEqual(36*sections+1,seconds*30);self.assertLess(36*sections+1-seconds*30,36)
if __name__=='__main__':unittest.main()
