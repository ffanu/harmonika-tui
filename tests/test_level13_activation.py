"""Activation/CAS/rollback model; no services, network or process signals."""
import contextlib,hashlib,importlib.util,io,json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
SOURCE=Path(__file__).resolve().parents[1]/'scripts/level13-activate.py'
class Activation(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.stage=self.root/'stage';self.base=self.root/'base';self.candidate=self.stage/'backend/planner';self.candidate.mkdir(parents=True);self.base.mkdir();self.old={};self.hashes={}
  for n in ['python-reference-spans.py','secret-firewall.cjs','planner-context.js','agent-controller.cjs','long-decision.cjs','internal-tool-planner.js','debug-workflow.cjs','decision-recovery.cjs']:
   (self.base/n).write_text('old '+n);self.old[n]=self.digest(self.base/n);(self.candidate/n).write_text('new '+n);self.hashes[n]=self.digest(self.candidate/n)
  n='proof-contract.cjs';(self.candidate/n).write_text('new proof');self.hashes[n]=self.digest(self.candidate/n)
  (self.stage/'expected-baseline.json').write_text(json.dumps(self.old));(self.stage/'verification.json').write_text(json.dumps({'candidate_sha256':self.hashes,'local_passed':True,'integration_passed':True,'activation_guards_passed':True,'level13_activation_guards_passed':True}));(self.stage/'proof-live-candidate.json').write_text(json.dumps([{'passed':True}]*7));(self.stage/'proof-live-candidate-manifest.json').write_text(json.dumps({n:self.hashes[n] for n in ['agent-controller.cjs','proof-contract.cjs','internal-tool-planner.js']}))
  spec=importlib.util.spec_from_file_location('l13_activation',SOURCE);self.mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(self.mod);self.mod.BASE=self.base;self.commands=[]
 def tearDown(self):self.tmp.cleanup()
 @staticmethod
 def digest(p):return hashlib.sha256(p.read_bytes()).hexdigest()
 def invoke(self,health=lambda:True):
  real_open=open;real_path=Path
  def safe_open(p,*a,**kw):return real_open(self.root/'lock' if str(p)=='/run/lock/harmonika-planner-activation.lock' else p,*a,**kw)
  def paths(p):return self.root/'backup' if str(p)=='/var/backups/harmonika-level13' else real_path(p)
  def run(argv,**kw):self.commands.append(argv)
  def replace(src,dst,mode):dst.write_bytes(src.read_bytes());dst.chmod(mode)
  with patch('builtins.open',safe_open),patch.object(self.mod,'Path',paths),patch.object(self.mod.sys,'argv',['activate',str(self.stage)]),patch.object(self.mod.subprocess,'run',run),patch.object(self.mod.g,'await_idle',lambda **kw:None),patch.object(self.mod.g,'pid',lambda n:'stable-'+n),patch.object(self.mod.g,'replace',replace),patch.object(self.mod.g,'health',health),contextlib.redirect_stdout(io.StringIO()):self.mod.main()
 def test_activate_verified_files_only(self):
  self.invoke();self.assertTrue(all(self.digest(self.base/n)==h for n,h in self.hashes.items()));self.assertEqual([c for c in self.commands if c[0]=='systemctl'],[['systemctl','restart','harmonika-ai']]);self.assertEqual(json.loads((self.stage/'activation.json').read_text())['status'],'active')
 def test_changed_baseline_blocks(self):
  (self.base/'agent-controller.cjs').write_text('external edit')
  with self.assertRaisesRegex(RuntimeError,'baseline_changed'):self.invoke()
  self.assertFalse((self.base/'proof-contract.cjs').exists())
 def test_existing_new_module_requires_reconcile(self):
  (self.base/'proof-contract.cjs').write_text('external module')
  with self.assertRaisesRegex(RuntimeError,'baseline_changed'):self.invoke()
  self.assertEqual((self.base/'proof-contract.cjs').read_text(),'external module')
 def test_canary_source_hash_mismatch_blocks(self):
  (self.candidate/'proof-contract.cjs').write_text('changed after live test')
  with self.assertRaisesRegex(RuntimeError,'live_candidate_source_changed'):self.invoke()
  self.assertFalse((self.base/'proof-contract.cjs').exists())
 def test_failed_canary_blocks(self):
  (self.stage/'proof-live-candidate.json').write_text(json.dumps([{'passed':False}]*7))
  with self.assertRaisesRegex(RuntimeError,'live_candidate_required'):self.invoke()
 def test_failed_health_rolls_back_and_removes_previously_absent_module(self):
  checks=iter([False,True])
  with self.assertRaisesRegex(RuntimeError,'health_failed'):self.invoke(lambda:next(checks))
  self.assertTrue(all(self.digest(self.base/n)==h for n,h in self.old.items()));self.assertFalse((self.base/'proof-contract.cjs').exists());self.assertEqual(json.loads((self.stage/'activation.json').read_text())['status'],'rolled_back')
 def test_rollback_never_overwrites_concurrent_edit(self):
  def health():(self.base/'agent-controller.cjs').write_text('concurrent operator edit');return False
  with self.assertRaisesRegex(RuntimeError,'health_failed'):self.invoke(health)
  self.assertEqual((self.base/'agent-controller.cjs').read_text(),'concurrent operator edit');self.assertEqual(json.loads((self.stage/'activation.json').read_text())['status'],'failed')
if __name__=='__main__':unittest.main()
