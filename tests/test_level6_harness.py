import base64,hashlib,importlib.util,json,subprocess,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
SRC=Path(__file__).resolve().parents[1]/'scripts/level6-lab'
class Harness(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
  self.meta={'lab_id':'hl6-unit','base_ref':'python@sha256:fixed','protected_build':{},'remote_root':'/root/owned'}
  (self.root/'lab.json').write_text(json.dumps(self.meta));(self.root/'controller.py').write_bytes((SRC/'controller.py').read_bytes())
  spec=importlib.util.spec_from_file_location('level6_controller_test',self.root/'controller.py');self.c=importlib.util.module_from_spec(spec);spec.loader.exec_module(self.c);self.c.S={'containers':[],'releases':{},'requests':{},'images':[],'active':'candidate','volume':'owned','closed':False}
 def tearDown(self):self.tmp.cleanup()
 def test_unregistered_container_rejected_before_docker(self):
  with patch.object(self.c,'docker') as d:
   with self.assertRaises(ValueError):self.c.owned('other')
   d.assert_not_called()
 def test_wrong_label_rejected(self):
  self.c.S['containers']=['candidate']
  with patch.object(self.c,'docker',return_value=json.dumps([{'Config':{'Labels':{'id.harmonika.level6':'other'}}}])):
   with self.assertRaises(ValueError):self.c.owned('candidate')
 def test_http_scope_before_operation(self):
  for r in [{'path':'http://external/'},{'path':'/jobs/x/../../admin'},{'path':'/jobs','method':'DELETE'},{'path':'/jobs','key':'x\r\nOops:y'},{'path':'/events','cursor':'1\nOops'}]:
   with patch.object(self.c,'run') as run,patch.object(self.c,'docker') as d:
    with self.assertRaises(ValueError):self.c.http(r)
    run.assert_not_called();d.assert_not_called()
 def test_build_context_scope(self):
  with patch.object(self.c,'run') as run:
   with self.assertRaises(ValueError):self.c.main({'op':'build','release':'queue-v1','files':{'../secret':'eA=='}})
   run.assert_not_called()
 def test_dockerfile_excludes_external_base_and_mount(self):
  for text in ['FROM other\n','FROM python@sha256:fixed\nADD https://x/ /app/\n','FROM python@sha256:fixed\nRUN --mount=type=bind echo x\n']:
   files={n:base64.b64encode(text.encode() if n=='Dockerfile' else b'x').decode() for n in self.c.FILES}
   with patch.object(self.c,'run') as run:
    with self.assertRaises(ValueError):self.c.main({'op':'build','release':'queue-v1','files':files})
    run.assert_not_called()
 def test_closed_lab_rejects_mutation(self):
  self.c.S['closed']=True
  with self.assertRaises(ValueError):self.c.main({'op':'restart'})
 def connector_process(self,body):
  code="import sys;sys.path.insert(0,"+repr(str(SRC))+");from effect_connector import EffectConnector,OutcomeUnknown;from pathlib import Path;r="+repr(str(self.root))+";e=EffectConnector(r);"+body
  return subprocess.run([sys.executable,'-I','-c',code],capture_output=True,text=True,timeout=10)
 def test_connector_commit_precedes_real_exit42(self):
  p=self.connector_process("Path(r,'crash-after-effect').write_text('armed');e.execute('job-1',{'kind':'report','text':'fixture'})")
  self.assertEqual(p.returncode,42)
  p=self.connector_process("assert e.lookup('job-1') is not None;import sqlite3;c=sqlite3.connect(str(Path(r)/'sink.sqlite'));assert c.execute('SELECT count(*) FROM effects').fetchone()[0]==1;assert c.execute(\"SELECT count(*) FROM calls WHERE kind='execute'\").fetchone()[0]==1")
  self.assertEqual(p.returncode,0,p.stderr)
 def test_unknown_ack_lookup_and_samekey_replay(self):
  p=self.connector_process("Path(r,'unknown-ack').write_text('armed');\ntry:e.execute('job-1',{'kind':'report','text':'fixture'})\nexcept OutcomeUnknown:pass\nelse:raise AssertionError('must_raise')\nassert e.lookup('job-1') is not None;e.execute('job-1',{'kind':'report','text':'fixture'});import sqlite3;c=sqlite3.connect(str(Path(r)/'sink.sqlite'));assert c.execute('SELECT count(*) FROM effects').fetchone()[0]==1")
  self.assertEqual(p.returncode,0,p.stderr)
if __name__=='__main__':unittest.main()
