import sys,unittest,tempfile,subprocess
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'runtime/tools'))
from skill_registry import Registry,SkillError
from skill_executor import SkillExecutor,ExecutionContext
class Terminal(unittest.IsolatedAsyncioTestCase):
 def test_registry_161_unique_remote_descriptors(self):
  r=Registry();v=[x for x in r.list() if x.get('worker')=='builder'];self.assertEqual(len(v),161);self.assertEqual(len(r.list()),601);self.assertTrue(all(x['execution_target']=='builder_project' for x in v))
  r.validate_input('scaffold.nextjs',{'project':'demo'})
 async def test_builder_cannot_execute_on_local_host(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp).resolve();ex=SkillExecutor(root/'state');ctx=ExecutionContext('fixture',str(root),frozenset({'scaffold.nextjs'}),True)
   with self.assertRaises(SkillError) as e:await ex.execute('scaffold.nextjs',{'project':'demo'},ctx)
   self.assertEqual(e.exception.code,'builder_project_required')
 def test_cli_help_and_launcher_dispatch(self):
  p=subprocess.run([sys.executable,'-I',str(ROOT/'runtime/tools/builder_cli.py'),'--help'],capture_output=True,text=True,timeout=5);self.assertEqual(p.returncode,0);self.assertIn('build',p.stdout);self.assertNotIn('hai_live_',p.stdout)
  self.assertIn("'builder','build'",(ROOT/'runtime/harmonika.py').read_text())
if __name__=='__main__':unittest.main()
