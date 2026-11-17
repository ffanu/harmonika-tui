"""Owner tool source parity, isolated RPC framing and portable local operations."""
import importlib.util
import builtins
import json
import os
from pathlib import Path
import subprocess
import sys
import shutil
import time
import tempfile
import unittest
from unittest.mock import patch,Mock

TOOLS=Path(__file__).resolve().parents[1]/'runtime/tools'
sys.path.insert(0,str(TOOLS))

def load(name):
    spec=importlib.util.spec_from_file_location('portable_'+name,TOOLS/(name+'.py'))
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module

class PortableOwnerMCP(unittest.TestCase):
    def test_actual_stdio_discovery_and_render_do_not_corrupt_jsonrpc(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);config=root/'config';downloads=root/'downloads'
            env=dict(os.environ,HARMONIKA_GLOBAL_CONFIG=str(config),HARMONIKA_CONFIG_DIR=str(config),HARMONIKA_DOWNLOADS_DIR=str(downloads),HARMONIKA_API_KEY='')
            frames=[{'jsonrpc':'2.0','id':1,'method':'tools/list'},
                    {'jsonrpc':'2.0','id':2,'method':'tools/call','params':{'name':'render','arguments':{'action':'typewriter','text':'UNFRAMED_SENTINEL'}}},
                    {'jsonrpc':'2.0','id':3,'method':'tools/call','params':{'name':'render','arguments':'INVALID_ARGUMENT_SENTINEL'}},
                    {'jsonrpc':'2.0','id':4,'method':'tools/call','params':{'name':'render','arguments':{'action':'measure','text':'漢字'}}}]
            run=subprocess.run([sys.executable,'-I',str(TOOLS/'mcp_server.py')],input='\n'.join(json.dumps(f) for f in frames)+'\n',capture_output=True,text=True,env=env,encoding='utf-8',timeout=20, errors='replace')
            self.assertEqual(run.returncode,0,run.stderr)
            replies=[json.loads(line) for line in run.stdout.splitlines()]
            self.assertEqual([r['id'] for r in replies],[1,2,3,4]);self.assertNotIn('UNFRAMED_SENTINEL',run.stdout)
            names={x['name'] for x in replies[0]['result']['tools']}
            self.assertTrue({'render','viz','os_intelligence','safe_install','interactive','api_intelligence','site_cloner','test_runner'}.issubset(names))
            self.assertEqual(json.loads(replies[3]['result']['content'][0]['text'])['display_width'],4)
            self.assertFalse(config.exists(),'discovery created persistent private state')
            self.assertFalse(downloads.exists(),'discovery created output directories')

    def test_mcp_resets_legacy_console_encoding_for_unicode_jsonrpc(self):
        with tempfile.TemporaryDirectory() as tmp:
            env=dict(os.environ,HARMONIKA_GLOBAL_CONFIG=tmp,HARMONIKA_DOWNLOADS_DIR=tmp,HARMONIKA_API_KEY='')
            bootstrap='import runpy,sys;sys.stdin.reconfigure(encoding="cp1252");sys.stdout.reconfigure(encoding="cp1252");runpy.run_path(sys.argv[1],run_name="__main__")'
            frame={'jsonrpc':'2.0','id':1,'method':'tools/call','params':{'name':'render','arguments':{'action':'measure','text':'漢字'}}}
            run=subprocess.run([sys.executable,'-I','-c',bootstrap,str(TOOLS/'mcp_server.py')],input=json.dumps(frame,ensure_ascii=False)+'\n',capture_output=True,text=True,encoding='utf-8',env=env,timeout=20)
            self.assertEqual(run.returncode,0,run.stderr)
            self.assertEqual(json.loads(json.loads(run.stdout)['result']['content'][0]['text'])['display_width'],4)

    def test_generated_browser_scripts_ignore_cp1252_default_encoding(self):
        native_open=builtins.open
        def cp1252_open(file,mode='r',*args,**kwargs):
            if 'b' not in mode and not args and 'encoding' not in kwargs:kwargs['encoding']='cp1252'
            return native_open(file,mode,*args,**kwargs)
        with tempfile.TemporaryDirectory() as tmp,patch.dict(os.environ,{'HARMONIKA_GLOBAL_CONFIG':tmp,'HARMONIKA_DOWNLOADS_DIR':tmp}):
            api=load('api_intel');clone=load('site_cloner')
            with patch.object(builtins,'open',side_effect=cp1252_open):
                discovery=Path(api._discover_script_path())
                engine=Path(clone._clone_script_path())
                daemon=Path(clone._ensure_daemon())
            self.assertEqual(discovery.read_text(encoding='utf-8'),api.DISCOVER_ENGINE_JS)
            self.assertEqual(engine.read_text(encoding='utf-8'),clone.CLONE_ENGINE_JS)
            self.assertEqual(daemon.read_text(encoding='utf-8'),clone._daemon_code())

    def test_render_import_does_not_require_posix_termios_or_tty(self):
        with patch.dict(sys.modules,{'termios':None,'tty':None}):
            render=load('render_engine')
            self.assertEqual(render.dw('漢字'),4)
            with patch.object(render.os,'name','nt'),patch.object(render,'is_tty',return_value=True):self.assertIsNone(render._query_bg_osc11())

    def test_local_windows_admin_is_explicit_and_never_runs_posix_commands(self):
        ops=load('ops_tools');osi=load('os_intel')
        with patch.object(ops.os,'name','nt'),patch.object(ops.subprocess,'run',side_effect=AssertionError('POSIX command executed')):
            result=ops.OPS_HANDLERS['server_health']({'target':'local'})
            self.assertFalse(result['ok']);self.assertEqual(result['error'],'unsupported_local_platform');self.assertFalse(result['executed'])
            result=osi.os_intelligence({'action':'detect','target':'local'})
            self.assertTrue(result['ok']);self.assertEqual(result['os']['id'],'windows')
            result=osi.safe_install({'target':'local','packages':'nginx'})
            self.assertFalse(result['ok']);self.assertEqual(result['error'],'unsupported_local_platform')

    def test_cannot_kill_unowned_processes_or_start_ssh_tunnels(self):
        ops=load('ops_tools')
        with patch.object(ops.subprocess,'run',side_effect=AssertionError('process signal')):
            result=ops.process_hunter({'action':'kill','pid':123})
            self.assertFalse(result['ok']);self.assertEqual(result['error'],'process_ownership_required')
        source=(TOOLS/'mcp_server.py').read_text(encoding='utf-8')
        self.assertNotIn('tunnel.exp',source)

    def test_watch_baseline_stays_in_memory(self):
        module=load('interactive')
        with tempfile.TemporaryDirectory() as tmp,patch.object(module,'WATCH_DIR',tmp),patch.object(module,'_get_handlers',return_value={'fixture':lambda args:{'ok':True,'value':'PRIVATE_SENTINEL'}}):
            result=module.watch({'tool':'fixture'})
            self.assertTrue(result['ok']);self.assertFalse(list(Path(tmp).iterdir()))

    def test_monitor_stop_is_registered_cooperative_and_never_global_pkill(self):
        with tempfile.TemporaryDirectory() as tmp,patch.dict(os.environ,{'HARMONIKA_GLOBAL_CONFIG':tmp,'HARMONIKA_DOWNLOADS_DIR':tmp}):
            api=load('api_intel');api._ensure_state()
            api._private_json(api.MONITOR_STATE,{'status':'running','run_id':'a'*32})
            with patch.object(api.subprocess,'run',side_effect=AssertionError('process signal')):
                result=api.monitor_stop({})
            self.assertEqual(result['status'],'stop_requested')
            signal=json.loads((Path(api.RUNTIME_DIR)/'monitor_stop.json').read_text(encoding='utf-8'));self.assertEqual(signal,{'run_id':'a'*32})
            api._private_json(api.MONITOR_STATE,{'status':'running','run_id':'a'*32})
            with patch.object(api.subprocess,'Popen',side_effect=AssertionError('duplicate monitor')):
                self.assertEqual(api.monitor_start({})['error'],'monitor_in_progress')
            self.assertNotIn('authorization\']',api.MONITOR_ENGINE_JS)
            self.assertIn('has_auth:',api.MONITOR_ENGINE_JS)
            self.assertNotIn("'pkill'",(TOOLS/'api_intel.py').read_text(encoding='utf-8'))

    @unittest.skipUnless(shutil.which('node'),'Node required for fixture browser worker')
    def test_actual_monitor_records_no_credentials_and_stops_its_own_worker(self):
        fake="""const E=require('events');exports.chromium={launch:async()=>{
const browser=new E(),context=new E();browser.close=async()=>browser.emit('disconnected');
context.newPage=async()=>({goto:async()=>context.emit('request',{url:()=> 'https://example.invalid/api/data?token=PRIVATE_SENTINEL',method:()=> 'GET',resourceType:()=> 'fetch',headers:()=> ({authorization:'Bearer PRIVATE_SENTINEL'})})});
browser.newContext=async()=>context;return browser;}};
"""
        with tempfile.TemporaryDirectory() as tmp:
            fake_path=Path(tmp)/'fake-playwright.cjs';fake_path.write_text(fake, encoding='utf-8')
            env={'HARMONIKA_GLOBAL_CONFIG':tmp,'HARMONIKA_DOWNLOADS_DIR':tmp,'HARMONIKA_PLAYWRIGHT_MODULE':str(fake_path),'HARMONIKA_NODE':shutil.which('node')}
            children=[];spawn=subprocess.Popen
            def owned_spawn(*args,**kwargs):
                child=spawn(*args,**kwargs);children.append(child);return child
            with patch.dict(os.environ,env):
                api=load('api_intel')
                try:
                    with patch.object(api.subprocess,'Popen',side_effect=owned_spawn):result=api.monitor_start({'url':'https://example.invalid'})
                    self.assertTrue(result['ok'])
                    capture=Path(api.RUNTIME_DIR)/'monitor_capture.json'
                    deadline=time.monotonic()+5
                    while not capture.exists() and time.monotonic()<deadline:time.sleep(0.05)
                    data=capture.read_text(encoding='utf-8');self.assertNotIn('PRIVATE_SENTINEL',data);self.assertNotIn('Bearer',data)
                    self.assertTrue(json.loads(data)['calls'][0]['has_auth'])
                    self.assertEqual(api.monitor_stop({})['status'],'stop_requested')
                    self.assertEqual(children[0].wait(timeout=5),0)
                    self.assertEqual(json.loads(Path(api.MONITOR_STATE).read_text(encoding='utf-8'))['status'],'stopped')
                finally:
                    for child in children:
                        if child.poll() is None:child.terminate();child.wait(timeout=5)  # Own fixture only.

    def test_browser_runtime_paths_and_platform_guard(self):
        with tempfile.TemporaryDirectory() as tmp,patch.dict(os.environ,{'HARMONIKA_GLOBAL_CONFIG':tmp,'HARMONIKA_DOWNLOADS_DIR':tmp}):
            site=load('site_cloner')
            with patch.object(site.sys,'platform','win32'),patch.object(site.subprocess,'Popen',side_effect=AssertionError('Xvfb on Windows')):
                result=site.vnc_bridge({'action':'start'})
            self.assertEqual(result['error'],'unsupported_local_platform')
            self.assertEqual(Path(site._clone_script_path()).parent,Path(tmp)/'site-cloner-runtime')
            self.assertIn('HARMONIKA_PLAYWRIGHT_MODULE',site.CLONE_ENGINE_JS)

if __name__=='__main__':unittest.main()
