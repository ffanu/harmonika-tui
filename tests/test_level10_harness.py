"""Negative boundary checks for protected Level10 infrastructure, not model code."""
import ast,hashlib,importlib.util,json,os,subprocess,sys,tempfile,time,unittest
from pathlib import Path
from email.message import Message
SRC=Path(__file__).resolve().parents[1]/'scripts/level10-lab'
class FixtureIdentity(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);(self.root/'access.json').write_text(json.dumps({'alpha':'fixture-alpha','beta':'fixture-beta'}));spec=importlib.util.spec_from_file_location('fixture_auth',SRC/'fixture_auth.py');self.m=importlib.util.module_from_spec(spec);spec.loader.exec_module(self.m);self.m.ROOT=self.root
 def tearDown(self):self.tmp.cleanup()
 def headers(self,*items):
  h=Message()
  for k,v in items:h[k]=v
  return h
 def test_private_bearer_resolves_both_owners(self):
  for owner in ['alpha','beta']:self.assertEqual(self.m.identity(self.headers(('Authorization',self.m.headers(owner)['Authorization']))),(owner,200))
 def test_forged_tenant_header_cannot_override_real_identity(self):self.assertEqual(self.m.identity(self.headers(('Authorization','Bearer fixture-beta'),('X-Lab-Owner','alpha'))),(None,403))
 def test_missing_invalid_and_ambiguous_credentials_rejected(self):
  for h in [self.headers(),self.headers(('Authorization','Bearer wrong')),self.headers(('Authorization','Bearer fixture-alpha'),('Authorization','Bearer fixture-beta')),self.headers(('Authorization','Bearer  fixture-alpha'))]:self.assertEqual(self.m.identity(h),(None,401))
 def test_nonascii_invalid_bearer_is_structured_unauthorized(self):self.assertEqual(self.m.identity(self.headers(('Authorization','Bearer caf\u00e9'))),(None,401))
 def test_ambiguous_owner_header_rejected(self):self.assertEqual(self.m.identity(self.headers(('Authorization','Bearer fixture-alpha'),('X-Lab-Owner','alpha'),('X-Lab-Owner','alpha'))),(None,403))
class MainProcessBoundary(unittest.TestCase):
 def test_real_exit42_is_durable_once_per_registered_role_generation(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);(root/'crash-generation.json').write_text(json.dumps({'generation':'generation-test'}));source=(SRC/'crash_guard.py').read_text().replace("Path('/data')",'Path('+repr(d)+')');script=root/'guard.py';script.write_text(source+'\nstart()\ntime.sleep(.4)\n');env={**os.environ,'WORKER_ID':'worker1','LAB_RELEASE':'fixture-release'}
   one=subprocess.run([sys.executable,str(script)],env=env,capture_output=True,timeout=5);self.assertEqual(one.returncode,42)
   two=subprocess.run([sys.executable,str(script)],env=env,capture_output=True,timeout=5);self.assertEqual(two.returncode,0)
   import sqlite3
   with sqlite3.connect(root/'chaos.sqlite') as c:self.assertEqual(c.execute('SELECT generation,role,release FROM crashes').fetchall(),[('generation-test','worker1','fixture-release')])
class StaticBoundaries(unittest.TestCase):
 def test_controller_never_accepts_user_docker_args_or_host_paths(self):
  s=(SRC/'controller.py').read_text();self.assertNotIn('shell=True',s);self.assertNotIn('sudo',s);self.assertIn("'--network',C.S['network']",s);self.assertIn("'--read-only'",s);self.assertIn('size=2097152',s);self.assertIn("'--cap-drop','ALL'",s);self.assertIn('C.authorize_request(request)',s)
 def test_storage_probe_uses_only_dedicated_bounded_tmpfs(self):
  s=(SRC/'storage_probe.py').read_text();self.assertIn("root=Path('/faultdisk')",s);self.assertIn('<=2097152',s);self.assertIn('errno.ENOSPC',s);self.assertNotIn("unlink('/data",s);self.assertNotIn('os.system',s)
 def test_holder_is_finite_and_releases_without_signals(self):
  s=(SRC/'holder.py').read_text();self.assertIn('time.monotonic()+45',s);self.assertNotIn('os.kill',s);self.assertNotIn('signal.',s);self.assertIn("('workflows','online_backup_probe')",s)
 def test_new_examiner_cannot_override_unittest_failure(self):
  tree=ast.parse((SRC/'independent.py').read_text());classes=[n for n in tree.body if isinstance(n,ast.ClassDef)];self.assertFalse(any(n.name in ['fail','assertEqual','assertTrue','run'] for c in classes for n in c.body if isinstance(n,ast.FunctionDef)));self.assertEqual(sum(n.name.startswith('test_') for c in classes for n in c.body if isinstance(n,ast.FunctionDef)),56)

class TemporalProofs(unittest.TestCase):
 def test_probe_formats_preserve_original_timing_and_reject_foreign_or_malformed_rows(self):
  tree=ast.parse((SRC.parent/'level10-grade.py').read_text());node=next(x for x in tree.body if isinstance(x,ast.FunctionDef) and x.name=='probe_witnesses');ns={};exec(compile(ast.Module(body=[node],type_ignores=[]),'probe_proof','exec'),ns);fn=ns['probe_witnesses'];base={'kind':'writer','origin':'agent','probe':'writer','action':'state','at':12.5,'started_at':10,'state':'holding','alive':True}
  for kind in ['writer','probe_state']:
   row={**base,'kind':kind};actual=fn([row],'writer');self.assertEqual(len(actual),1);self.assertEqual(actual[0]['at'],12.5);self.assertEqual(actual[0]['kind'],'probe_state');self.assertEqual(row['kind'],kind)
  for bad in [{'origin':'examiner'},{'probe':'lock'},{'kind':'lock'},{'kind':'probe_control'},{'at':True},{'at':9},{'started_at':False},{'alive':'true'},{'action':'release'},{'state':'starting'}]:self.assertEqual(fn([{**base,**bad}],'writer'),[])
 def fixture(self):
  source=ast.parse((SRC.parent/'level10-grade.py').read_text());node=next(x for x in ast.walk(source) if isinstance(x,ast.FunctionDef) and x.name=='rolling_window');wid='a'*32;h=hashlib.sha256(wid.encode()).hexdigest();release='recovery-v4';physical={'running':True,'image_id':'legacy-image','container_sha256':'same-owned-id'};start={'kind':'rolling','operation':'upgrade','at':100,'audit_floor':10,'roles':{'coordinator1':physical},'roles_replaced':['api','coordinator2'],'unchanged_roles':{'coordinator1':True}};end={'kind':'rolling','operation':'roll','at':120};proof={'kind':'rolling_preflight','origin':'controller','parent_origin':'agent','release':release,'at':119,'running':True,'container_sha256':'same-owned-id','image_id':'legacy-image','workflow_states':{h:'completed'},'claim_trace':[{'id':11,'coordinator_id':'coordinator1','workflow_id_sha256':h,'task':'prepare','epoch':2,'event':'claim'},{'id':12,'coordinator_id':'coordinator1','workflow_id_sha256':h,'task':'prepare','epoch':2,'event':'settle'}]};ns={'current':[start,end],'obs':[proof],'accepted':[{'workflow_id':wid,'key':release+'-rolling'}],'release':release,'hashlib':hashlib,'events':{'containers':[{'container_sha256':'same-owned-id','events':[]}]}};exec(compile(ast.Module(body=[node],type_ignores=[]),'rolling_proof','exec'),ns);return ns
 def test_legacy_claim_binding_rejects_old_version_claim_or_fake_parent(self):
  self.assertTrue(self.fixture()['rolling_window']('upgrade','coordinator1','legacy-image'))
  ns=self.fixture();ns['events']['containers'][0]['events']=[{'action':'die','at':119.5}];self.assertTrue(ns['rolling_window']('upgrade','coordinator1','legacy-image'))
  for bad in ['old_claim','wrong_parent','wrong_pid','dead','late','wrong_epoch','died']:
   ns=self.fixture();p=ns['obs'][0]
   if bad=='old_claim':
    for t in p['claim_trace']:t['id']-=10
   if bad=='wrong_parent':p['parent_origin']='examiner'
   if bad=='wrong_pid':p['container_sha256']='other'
   if bad=='dead':p['running']=False
   if bad=='late':p['at']=121
   if bad=='wrong_epoch':p['claim_trace'][1]['epoch']=3
   if bad=='died':ns['events']['containers'][0]['events']=[{'action':'die','at':110}]
   with self.subTest(bad=bad),self.assertRaises(AssertionError):ns['rolling_window']('upgrade','coordinator1','legacy-image')
 def helpers(self):
  source=ast.parse((SRC.parent/'level10-grade.py').read_text());nodes=[x for x in source.body if isinstance(x,ast.FunctionDef) and x.name in ['eligible_replay_admissions','safe_reconcile_receipt']];ns={'hashlib':hashlib};exec(compile(ast.Module(body=nodes,type_ignores=[]),'clone_proof','exec'),ns);return ns
 def test_clone_requires_requests_acked_by_native_before_that_reconcile(self):
  ns=self.helpers();release='recovery-v4';admissions=[{'seq':i,'owner':owner,'key':release+'-'+case} for i,owner,case in [(2,'alpha','after-a'),(3,'beta','after-b'),(4,'alpha','future')]];observations=[{'origin':'agent','release':release,'kind':'http','method':'POST','route':'/workflows','status':202,'at':105+i,'owner_key_sha256':hashlib.sha256((a['owner']+'\0'+a['key']).encode()).hexdigest()} for i,a in enumerate(admissions)];observations[-1]['at']=115
  self.assertEqual(ns['eligible_replay_admissions'](admissions,observations,release,100,110,1),admissions[:2]);observations[0]['origin']='examiner';self.assertEqual(ns['eligible_replay_admissions'](admissions,observations,release,100,110,1),admissions[1:2])
 def test_reconciliation_claim_cannot_hide_new_post_or_effect(self):
  import copy
  fn=self.helpers()['safe_reconcile_receipt'];base={'result':{'complete':True,'active':False,'unresolved':0},'effect_witness':{'before':{'effects':4,'child_posts':4,'child_lookups':3},'after':{'effects':4,'child_posts':4,'child_lookups':5}}};self.assertTrue(fn(base))
  for bad in ['effects','posts','no_lookup','active','unknown','unfinished','missing']:
   v=copy.deepcopy(base)
   if bad=='effects':v['effect_witness']['after']['effects']=5
   if bad=='posts':v['effect_witness']['after']['child_posts']=5
   if bad=='no_lookup':v['effect_witness']['after']['child_lookups']=3
   if bad=='active':v['result']['active']=True
   if bad=='unknown':v['result']['unresolved']=1
   if bad=='unfinished':v['result']['complete']=False
   if bad=='missing':v.pop('effect_witness')
   with self.subTest(bad=bad):self.assertFalse(fn(v))

if __name__=='__main__':unittest.main(verbosity=2)
