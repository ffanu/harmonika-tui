import asyncio,importlib.util,json,pathlib,subprocess,sys,tempfile,unittest
from unittest.mock import patch
ROOT=pathlib.Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'backend'))
from web_builder.fullstack_template import generate,SHA
from web_builder.generation import dispatch
from web_builder.common import BuilderError
class FullstackTemplate(unittest.TestCase):
 def test_real_archive_generation_and_safe_overrides(self):
  r=dispatch('scaffold.nextjs',{'project':'test-app','template':'harmonika-fullstack'});files={x['path']:x['content'] for x in r['writes']};package=json.loads(files['test-app/package.json']);self.assertNotIn('z-ai-web-dev-sdk',package['dependencies']);self.assertNotIn('db:push',package['scripts']);self.assertTrue(any(p.endswith('/src/components/ui/button.tsx') for p in files));self.assertFalse(any('/.zscripts/' in p for p in files));self.assertFalse(any('ignoreBuildErrors' in c or '--accept-data-loss' in c for c in files.values()));self.assertEqual(r['data']['source_sha256'],SHA)
 def test_tampered_archive_and_bad_project_rejected(self):
  with tempfile.TemporaryDirectory() as tmp:
   p=pathlib.Path(tmp)/'bad.tar';p.write_bytes(b'invalid')
   with patch('web_builder.fullstack_template.ARCHIVE',p),self.assertRaises(BuilderError):generate('test-app')
  for name in ['../escape','/absolute','bad/name','a','bad name']:
   with self.subTest(name=name),self.assertRaises(BuilderError):generate(name)
 def test_runtime_scripts_syntax_and_real_port_reservation(self):
  r=generate('test-app');files=dict(r['writes'])
  with tempfile.TemporaryDirectory() as tmp:
   root=pathlib.Path(tmp)
   for name in ('harmonika-serve.mjs','copy-static.mjs'):
    p=root/name;p.write_text(files['test-app/scripts/'+name]);subprocess.run(['node','--check',str(p)],check=True,capture_output=True)
   js="""import net from 'node:net';import assert from 'node:assert/strict';import {freePort,checkPort} from './harmonika-serve.mjs';
for(const p of [3000,0,-1,65536,'bad','3000'])assert.throws(()=>checkPort(p));
const occupied=net.createServer();await new Promise(resolve=>occupied.listen(0,'127.0.0.1',resolve));
await assert.rejects(freePort(occupied.address().port));
const port=await freePort();assert(port!==3000&&port>=1024);const test=net.createServer();await new Promise((resolve,reject)=>{test.once('error',reject);test.listen(port,'127.0.0.1',resolve);});test.close();occupied.close();console.log('passed');"""
   (root/'test.mjs').write_text(js);p=subprocess.run(['node',str(root/'test.mjs')],capture_output=True,text=True,timeout=15);self.assertEqual(p.returncode,0,p.stderr);self.assertIn('passed',p.stdout)
 def test_workflow_automatically_selects_new_template(self):
  from web_builder.workflow import build
  r=asyncio.run(build(None,{'spec':'Build a Next.js website','name':'test-app','stack':'auto'}, {},'synthetic-job','synthetic-actor'));files={x['path']:x['content'] for x in r['writes']};self.assertIn('test-app/TEMPLATE-PROVENANCE.json',files);self.assertFalse(r['data']['deployed']);self.assertIn('test-app/scripts/harmonika-serve.mjs',files)
 def test_builtin_still_explicitly_available(self):
  r=dispatch('scaffold.nextjs',{'project':'test-app','template':'builtin','port':38111});self.assertEqual(r['data']['framework'],'nextjs');self.assertNotIn('template',r['data'])
