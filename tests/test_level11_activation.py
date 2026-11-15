"""Exercise durable activation/rollback control on synthetic files, with no real service calls."""
import base64,hashlib,importlib.util,io,json,shutil,subprocess,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
spec=importlib.util.spec_from_file_location('finish',Path(__file__).resolve().parents[1]/'scripts/level11-finish-backend.py');finish=importlib.util.module_from_spec(spec);spec.loader.exec_module(finish)
class Activation(unittest.TestCase):
 def scenario(self,fail=False,partial=False):
  temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup);root=Path(temp.name);stage=root/'stage';scripts=stage/'scripts';scripts.mkdir(parents=True);api=root/'api';api.mkdir();backup=root/'backup';backup.mkdir();(root/'locks').mkdir();names=['module'+str(n) for n in range(8)];old={n:(n+' old').encode() for n in names};new={n:(n+' new').encode() for n in names}
  for n in names:(api/n).write_bytes(old[n]);(backup/n).write_bytes(old[n])
  hashes={n:hashlib.sha256(b).hexdigest() for n,b in new.items()};(stage/'verification.json').write_text(json.dumps({'candidate_sha256':hashes}));g="""import hashlib,shutil
from pathlib import Path
def digest(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def pid(unit):return '123'
def await_idle(*a,**k):pass
def health():return True
def replace(source,target,mode):
 if PARTIAL and target.name=='module3':raise OSError('synthetic_replace_failure')
 shutil.copyfile(source,target)
""".replace('PARTIAL',repr(partial));(scripts/'activate-debug-workflow.py').write_text(g);calls=[]
  def run(args,**kw):
   calls.append(args)
   if args[0]=='python3':
    for n in names:(api/n).write_bytes(new[n])
    (stage/'activation.json').write_text(json.dumps({'status':'active','backup':str(backup),'candidate_sha256':hashes}));return subprocess.CompletedProcess(args,0,'','')
   if args[0]=='node':
    decision=Path(args[1]).name=='level11-decision-smoke.cjs';rows=[{'passed':not(fail and decision),'client_tools_executed':False,'internal_state_exposed':False} for _ in range(8 if decision else 5)];(stage/('level11-model-smoke.json' if decision else 'source-after.json')).write_text(json.dumps(rows));return subprocess.CompletedProcess(args,int(fail and decision),'mechanical probe','')
   if args==['systemctl','restart','harmonika-ai']:return subprocess.CompletedProcess(args,0,'','')
   raise AssertionError('unexpected external operation')
  code=finish.REMOTE.replace('/opt/harmonika-ai/api',str(api)).replace('/run/lock/',str(root/'locks')+'/');payload={'stage':str(stage),'hashes':hashes,'canary':base64.b64encode(b'synthetic-canary').decode()}
  with patch('sys.stdin',io.StringIO(json.dumps(payload))),patch('sys.stdout',io.StringIO()),patch('subprocess.run',side_effect=run):
   try:exec(compile(code,'isolated-remote-finish','exec'),{})
   except SystemExit as e:self.assertEqual(e.code,1)
  return json.loads((stage/'level11-finish-state.json').read_text()),{n:(api/n).read_bytes() for n in names},old,new,calls
 def test_happy_path_persists_verified_state(self):
  state,files,old,new,calls=self.scenario();self.assertEqual(state['status'],'active');self.assertEqual(files,new);self.assertFalse(any(c[0]=='systemctl' for c in calls))
 def test_failed_canary_restores_all_hashes_and_rechecks_baseline(self):
  state,files,old,new,calls=self.scenario(fail=True);self.assertEqual(state['status'],'rolled_back');self.assertTrue(state['rollback_healthy']);self.assertEqual(files,old);self.assertEqual(len(state['rollback_source_canaries']),5);self.assertEqual(sum(c[0]=='node' for c in calls),3);self.assertEqual(sum(c[0]=='systemctl' for c in calls),1)
 def test_partial_restore_records_failure_without_false_health(self):
  state,files,old,new,calls=self.scenario(fail=True,partial=True);self.assertEqual(state['status'],'rollback_failed');self.assertNotIn('rollback_healthy',state);self.assertNotEqual(files,new);self.assertNotEqual(files,old);self.assertFalse(any(c[0]=='systemctl' for c in calls))
if __name__=='__main__':unittest.main(verbosity=2)
