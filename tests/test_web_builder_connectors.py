import os,tempfile,unittest,json,asyncio
from pathlib import Path
from unittest.mock import patch
from backend.web_builder.connectors import Connectors,dispatch
from backend.web_builder.common import BuilderError
class Connections(unittest.IsolatedAsyncioTestCase):
 async def test_missing_connector_is_explicit_not_fake_connected(self):
  with tempfile.TemporaryDirectory() as d:
   c=Connectors(Path(d)/'absent');self.assertFalse(c.ready)
   with self.assertRaises(BuilderError) as e:await dispatch(c,'ssh.connect',{'target':'customer'}, {},'job','api-token:1')
   self.assertEqual(e.exception.code,'ssh_connector_not_configured')
 async def test_target_principal_isolation_precedes_subprocess(self):
  c=Connectors('/nonexistent');c.ready=True;c.targets={'customer':{'principals':['api-token:1']}}
  with self.assertRaises(BuilderError) as e:await dispatch(c,'ssh.connect',{'target':'customer'},{},'job','api-token:2')
  self.assertEqual(e.exception.code,'ssh_target_not_authorized')
 async def test_setup_is_a_recipe_and_never_resets_firewall(self):
  r=await dispatch(None,'ssh.setup.firewall',{}, {},'job','api-token:1');self.assertFalse(r['data']['executed']);self.assertNotIn('ufw reset',r['writes'][0]['content']);self.assertNotIn('ufw disable',r['writes'][0]['content'])
 def test_keys_are_not_selected_by_client(self):
  c=Connectors('/nonexistent');c.targets={'bad':{'principals':['api-token:1'],'user':'root','host':'server'}}
  with self.assertRaises(BuilderError) as e:c.target('bad','api-token:1')
  self.assertEqual(e.exception.code,'ssh_unprivileged_account_required')
 def test_registration_cannot_be_world_readable(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'config';p.write_text('{}');p.chmod(0o644)
   with self.assertRaises(BuilderError):Connectors(p)

class RemoteFileProgram(unittest.TestCase):
 def execute(self,mode,root,path,data=b''):
  import subprocess,sys
  from backend.web_builder.connectors import REMOTE_FILE_IO
  return subprocess.run([sys.executable,'-c',REMOTE_FILE_IO,mode,str(root),path],input=data,capture_output=True)
 def test_upload_roundtrip_no_overwrite_and_no_partial_target(self):
  import base64
  with tempfile.TemporaryDirectory() as t:
   root=Path(t).resolve();r=self.execute('upload',root,'file.txt',b'fixture');self.assertEqual(r.returncode,0);r=self.execute('upload',root,'file.txt',b'other');self.assertNotEqual(r.returncode,0);self.assertEqual((root/'file.txt').read_bytes(),b'fixture');self.assertFalse(list(root.glob('.harmonika-upload-*')))
   r=self.execute('download',root,'file.txt');self.assertEqual(base64.b64decode(json.loads(r.stdout)['bytes_base64']),b'fixture')
 def test_symlink_directory_never_reads_outside_target(self):
  with tempfile.TemporaryDirectory() as t:
   root=Path(t).resolve();(root/'inside').mkdir();(root/'outside').mkdir();(root/'outside'/'secret').write_text('PRIVATE_FIXTURE');(root/'inside'/'link').symlink_to(root/'outside',target_is_directory=True)
   r=self.execute('download',root/'inside','link/secret');self.assertNotEqual(r.returncode,0);self.assertNotIn(b'PRIVATE_FIXTURE',r.stdout)
