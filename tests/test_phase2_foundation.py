import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from backend.coding_runtime.files import FileStore
from backend.coding_runtime.phase2_foundation import plan,FoundationError,patch_existing,unified

class Foundation(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name).resolve();self.fs=FileStore(self.root)
        self.fs.execute('code.write',{'path':'app.py','content':'def total():\n    return 1\n'})
    def tearDown(self):self.tmp.cleanup()
    def apply_to_fixture(self,result):
        for w in result['writes']:
            args={'path':w['path'],'content':w['content']}
            if w['mode']=='replace':args.update(overwrite=True,expected_sha256=w['expected_sha256'])
            self.fs.execute('code.write',args)
    def test_diff_does_not_mutate(self):
        before=self.fs.snapshot();p=plan('code.diff',{'path':'app.py','new_content':'def total():\n    return 2\n'},self.fs)
        self.assertEqual(before,self.fs.snapshot());self.assertEqual(p['writes'],[]);self.assertIn('+    return 2',p['diff'])
    def test_patch_is_hash_bound_and_roundtrips(self):
        read=self.fs.execute('code.read',{'path':'app.py'})
        diff=unified('app.py',read['content'],'def total():\n    return 42\n')
        p=plan('code.patch',{'path':'app.py','patch':diff,'expected_sha256':read['sha256']},self.fs)
        self.assertIn('return 1',self.fs.execute('code.read',{'path':'app.py'})['content'])
        self.apply_to_fixture(p);self.assertIn('return 42',self.fs.execute('code.read',{'path':'app.py'})['content'])
    def test_stale_hash_rejected(self):
        with self.assertRaises(FoundationError) as e:plan('code.patch',{'path':'app.py','patch':'x','expected_sha256':'0'*64},self.fs)
        self.assertEqual(e.exception.code,'hash_mismatch')
    def test_context_conflict_preserves_source(self):
        before=self.fs.snapshot()
        with self.assertRaises(FoundationError):patch_existing('app.py','wrong\n','--- a/app.py\n+++ b/app.py\n@@ -1 +1 @@\n-before\n+after\n')
        self.assertEqual(before,self.fs.snapshot())
    def test_patch_insertion_deletion_and_multiple_hunks(self):
        for before,after in [('a\nb\n','x\na\nb\n'),('a\nb\n','b\n'),('a\nb\n',''),('', 'a\n'),('\n'.join(str(x) for x in range(40))+'\n','first\n'+'\n'.join(str(x) for x in range(1,39))+'\nlast\n')]:
            with self.subTest(before=before[:20]):self.assertEqual(patch_existing('test.py',before,unified('test.py',before,after)),after)
    def test_other_file_and_traversal_patch_denied(self):
        for target in ('other.py','../../escape.py','/etc/passwd'):
            with self.assertRaises(FoundationError):patch_existing('app.py','a\n','--- a/'+target+'\n+++ b/'+target+'\n@@ -1 +1 @@\n-a\n+b\n')
    def test_malformed_hunks_rejected(self):
        for body in ('@@ -1,2 +1 @@\n-a\n+b\n','@@ -1 +8 @@\n-a\n+b\n','@@ -0 +1 @@\n-a\n+b\n','@@ -1 +1 @@\n-a\n+b\n\\ No newline at end of file\n'):
            with self.subTest(body=body),self.assertRaises(FoundationError):patch_existing('a.py','a\n','--- a/a.py\n+++ b/a.py\n'+body)
    def test_eof_unsupported_is_explicit(self):
        with self.assertRaises(FoundationError) as e:patch_existing('a.py','a','x')
        self.assertEqual(e.exception.code,'patch_newline_unsupported')
    def test_scaffold_python_plan_is_pure_then_generated_tests_run(self):
        before=self.fs.snapshot();p=plan('scaffold.project',{'path':'starter','template':'python_http','name':'Demo'},self.fs)
        self.assertEqual(before,self.fs.snapshot());self.assertEqual(len(p['writes']),3);self.assertFalse(p['production_registered'])
        self.apply_to_fixture(p)
        result=subprocess.run([sys.executable,'-B','-m','unittest','discover'],cwd=self.root/'starter',capture_output=True,text=True,timeout=10)
        self.assertEqual(result.returncode,0,result.stderr);self.assertIn('OK',result.stderr)
    def test_scaffold_node_generated_tests_run(self):
        node=shutil.which('node')
        if not node:self.skipTest('Node unavailable')
        p=plan('scaffold.project',{'path':'nodeapp','template':'node_http','name':'Demo'},self.fs);self.apply_to_fixture(p)
        r=subprocess.run([node,'--test'],cwd=self.root/'nodeapp',capture_output=True,text=True,timeout=10)
        self.assertEqual(r.returncode,0,r.stderr);self.assertIn('pass 1',r.stdout)
    def test_scaffold_existing_file_never_overwritten(self):
        self.fs.execute('code.write',{'path':'starter/app.py','content':'existing'})
        before=self.fs.snapshot()
        with self.assertRaises(FoundationError):plan('scaffold.project',{'path':'starter','template':'python_http','name':'Demo'},self.fs)
        self.assertEqual(before,self.fs.snapshot())
    def test_component_template_has_validated_name(self):
        r=plan('scaffold.component',{'path':'StatusCard.jsx','name':'StatusCard'},self.fs)
        self.assertIn('export default function StatusCard',r['writes'][0]['content'])
        self.assertIn('requires',r['notes'][0])
        with self.assertRaises(FoundationError):plan('scaffold.component',{'path':'x.jsx','name':"X';process.exit()"},self.fs)
    def test_api_template_compiles_and_declares_route(self):
        r=plan('scaffold.api',{'path':'routes/health.py','route':'/health','method':'GET'},self.fs)
        compile(r['writes'][0]['content'],'generated-health.py','exec');self.assertIn("ROUTE = '/health'",r['writes'][0]['content'])
    def test_invalid_inputs_rejected_without_mutation(self):
        before=self.fs.snapshot()
        for skill,args in [('scaffold.project',{'path':'../escape','template':'python_http','name':'Demo'}),('scaffold.project',{'path':'new','template':'nextjs','name':'Demo'}),('scaffold.api',{'path':'a.py','route':"/x\nmalicious",'method':'GET'}),('scaffold.api',{'path':'a.py','route':'/x','method':'DELETE'}),('code.diff',{'path':'.env','new_content':'x'}),('code.diff',{'path':'app.py','new_content':'x','unexpected':1})]:
            with self.subTest(skill=skill,args=args),self.assertRaises(Exception):plan(skill,args,self.fs)
        self.assertEqual(before,self.fs.snapshot())
    def test_no_production_registration_added(self):
        from backend.coding_runtime.catalog import BASE_SKILLS,SKILLS
        self.assertEqual(len(BASE_SKILLS),18)
        self.assertNotIn('code.patch',BASE_SKILLS)
        self.assertIn('code.patch',SKILLS)
