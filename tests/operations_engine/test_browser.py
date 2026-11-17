import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from backend.operations_engine import browser
MODULE=os.environ.get('HARMONIKA_TEST_PLAYWRIGHT_MODULE',str(Path.home()/'.config/harmonika-terminal/browser-runtime/node_modules/playwright'))
NODE=os.environ.get('HARMONIKA_TEST_BROWSER_NODE') or shutil.which('node')
FIXTURE='''<!doctype html><html><body><input data-testid="entry"><button data-testid="save" onclick="document.querySelector('#result').textContent=document.querySelector('input').value">Save</button><div id="result">Empty</div></body></html>'''

class Browser(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
  self.base=Path(self.temp.name).resolve();self.project=self.base/'project';self.project.mkdir();self.artifacts=self.base/'artifacts';self.artifacts.mkdir(mode=0o700)
  (self.project/'index.html').write_text(FIXTURE,encoding='utf-8')
  self.config={'project_root':str(self.project),'artifacts_root':str(self.artifacts),'browser_policy':True,'tools':{'node':NODE,'playwright_module':MODULE}}
  self.steps=[{'action':'fill','selector':{'by':'test_id','value':'entry'},'value':'Fixture value'},{'action':'click','selector':{'by':'test_id','value':'save'}},{'action':'assert_text','selector':{'by':'css','value':'#result'},'value':'Fixture value'}]
 def runtime(self):
  if not browser._runtime(self.config):self.skipTest('actual registered Playwright Chromium missing')
 def test_missing_runtime_explicit_and_no_artifact(self):
  self.config['tools']={};result=browser.run(self.config,{'path':'index.html','steps':self.steps})
  self.assertEqual(result['status'],'needs_toolchain');self.assertEqual(list(self.artifacts.iterdir()),[])
 def test_scripts_external_paths_no_assertion_and_private_paths_denied(self):
  for payload in ({'path':'../index.html','steps':self.steps},{'path':'https://example.com','steps':self.steps},{'path':'.env','steps':self.steps},{'path':'index.html','steps':[{'action':'evaluate','value':'process.env'}]},{'path':'index.html','steps':self.steps[:2]}):
   with self.assertRaises(ValueError):browser.run(self.config,payload)
  self.config['browser_policy']=False
  with self.assertRaisesRegex(ValueError,'browser_not_allowed'):browser.run(self.config,{'path':'index.html','steps':self.steps})
 def test_actual_browser_assertions_private_trace_and_24h_ttl(self):
  self.runtime();result=browser.run(self.config,{'path':'index.html','steps':self.steps,'viewport':{'width':390,'height':844}})
  self.assertEqual(result['status'],'passed',result);self.assertTrue(result['execution_performed']);self.assertEqual(result['assertions_requested'],1);self.assertEqual(len(result['artifacts']),2)
  out=self.artifacts/result['artifacts'][0]['id'].split('/')[0];self.assertEqual({f.name for f in out.iterdir()},{'trace.zip','screenshot.png','retention.json'})
  for artifact in result['artifacts']:
   file=self.artifacts/artifact['id'];self.assertTrue(file.is_file());self.assertGreater(artifact['bytes'],0);self.assertTrue(artifact['private'])
   retention=json.loads((file.parent/'retention.json').read_text(encoding='utf-8'));self.assertEqual(retention['expires_at']-retention['created_at'],86400)
   if os.name!='nt':self.assertEqual(file.stat().st_mode&0o777,0o600)
  self.assertNotIn('Fixture value',json.dumps(result));self.assertNotIn(str(self.project),json.dumps(result))
 def test_actual_failed_assertion_and_credential_field_never_pass(self):
  self.runtime();result=browser.run(self.config,{'path':'index.html','steps':[{'action':'assert_text','selector':{'by':'css','value':'#result'},'value':'Wrong'}]})
  self.assertEqual(result['status'],'failed');self.assertFalse(result['steps'][0]['passed']);self.assertNotIn('Wrong',json.dumps(result))
  (self.project/'index.html').write_text('<input type="password" data-testid="entry"><p id="result">ok</p>',encoding='utf-8')
  result=browser.run(self.config,{'path':'index.html','steps':[self.steps[0],{'action':'assert_visible','selector':{'by':'css','value':'#result'}}]})
  self.assertEqual(result['status'],'failed');self.assertFalse(result['steps'][0]['passed'])
 def test_actual_external_fetch_websocket_blocked(self):
  self.runtime();html='<p id="result">ok</p><script>fetch("http://127.0.0.1:1/private").catch(()=>{});try{new WebSocket("ws://127.0.0.1:1/private")}catch{}</script>'
  (self.project/'index.html').write_text(html,encoding='utf-8');result=browser.run(self.config,{'path':'index.html','steps':[{'action':'assert_visible','selector':{'by':'css','value':'#result'}}]})
  self.assertEqual(result['status'],'failed');self.assertTrue(result['blocked_requests'] or result['page_errors'])

 def test_actual_popups_downloads_and_websockets_are_stopped(self):
  self.runtime()
  fixtures=[('<button data-testid="action" onclick="window.open(\'/child.html\',\'_blank\')">Open</button><p id="result">ok</p>','blocked_popups'),('<a data-testid="action" download="private-download.txt" href="sample.txt">Download</a><p id="result">ok</p>','blocked_downloads'),('<button data-testid="action" onclick="new WebSocket(location.origin.replace(\'http\',\'ws\'))">Socket</button><p id="result">ok</p>','blocked_requests')]
  (self.project/'child.html').write_text('<p>child</p>',encoding='utf-8');(self.project/'sample.txt').write_text('fixture',encoding='utf-8')
  for html,counter in fixtures:
   with self.subTest(counter=counter):
    (self.project/'index.html').write_text(html,encoding='utf-8')
    steps=[{'action':'click','selector':{'by':'test_id','value':'action'}},{'action':'assert_visible','selector':{'by':'css','value':'#result'}}]
    result=browser.run(self.config,{'path':'index.html','steps':steps});self.assertEqual(result['status'],'failed');self.assertGreater(result[counter],0)
  self.assertFalse(list(self.artifacts.rglob('private-download.txt')))

if __name__=='__main__':unittest.main()
