"""Exercise actual native shell receipt hooks against a loopback fixture, no real provider."""
from pathlib import Path
import http.server,threading,subprocess,tempfile,json,os,time

def run_native_receipt_smoke(binary, plugin, work_parent, base_env):
 plugin=Path(plugin).resolve()
 with tempfile.TemporaryDirectory(prefix='harmonika-receipt-canary-',dir=work_parent) as tmp:
  root=Path(tmp);cfg=root/'config';cfg.mkdir()
  (root/'receipt.test.cjs').write_text("const {test}=require('node:test');const assert=require('node:assert/strict');test('fixture sum',()=>assert.equal(2+2,4));test('fixture string',()=>assert.equal('a'.toUpperCase(),'A'));\n")
  seen=[];errors=[]
  class Handler(http.server.BaseHTTPRequestHandler):
   def log_message(self,*args):pass
   def do_POST(self):
    body=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
    tools=[m for m in body.get('messages',[]) if m.get('role')=='tool']
    report={'has_result':bool(tools),'declared_shell':any(t.get('function',{}).get('name')=='shell' for t in body.get('tools',[]))}
    if tools:
     try:value=json.loads(tools[-1]['content'])
     except (ValueError,TypeError):value={}
     meta=value.get('metadata',{}) if isinstance(value,dict) else {}
     report.update(exit_code=meta.get('exit_code'),status=meta.get('status'),truncated=meta.get('truncated'),output_has_two_tests='# tests 2' in value.get('output',''))
    seen.append(report)
    call=not tools and report['declared_shell']
    delta={'role':'assistant','tool_calls':[{'index':0,'id':'fixture_receipt_call','type':'function','function':{'name':'shell','arguments':json.dumps({'command':'node --test receipt.test.cjs','workdir':str(root)})}}]} if call else {'role':'assistant','content':'HARMONIKA_RECEIPT_CANARY_OK'}
    response={'id':'chatcmpl_fixture','object':'chat.completion.chunk','created':1,'model':'harmonika-ai-codex','choices':[{'index':0,'delta':delta,'finish_reason':None}]}
    end={**response,'choices':[{'index':0,'delta':{},'finish_reason':'tool_calls' if call else 'stop'}]}
    raw=('data: '+json.dumps(response)+'\n\ndata: '+json.dumps(end)+'\n\ndata: [DONE]\n\n').encode()
    self.send_response(200);self.send_header('content-type','text/event-stream');self.send_header('content-length',str(len(raw)));self.end_headers();self.wfile.write(raw)
  server=http.server.ThreadingHTTPServer(('127.0.0.1',0),Handler);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
  config={'model':'harmonika/harmonika-ai-codex','small_model':'harmonika/harmonika-ai-codex','enabled_providers':['harmonika'],'share':'disabled','plugins':[{'package':str(plugin)}],'provider':{'harmonika':{'name':'Fixture','npm':'@ai-sdk/openai-compatible','options':{'baseURL':f'http://127.0.0.1:{server.server_port}/v1','apiKey':'fixture-no-real-key'},'models':{'harmonika-ai-codex':{'name':'Fixture','tool_call':True,'limit':{'context':872000,'output':4096}}}}}}
  (cfg/'opencode.json').write_text(json.dumps(config));(cfg/'service-stable.json').write_text('{"disabled":true}')
  env={**base_env,'HARMONIKA_GLOBAL_CONFIG':str(cfg),'HARMONIKA_CONFIG_DIR':str(cfg),'OPENCODE_CONFIG_DIR':str(cfg),'OPENCODE_CONFIG':str(cfg/'opencode.json'),'HARMONIKA_GLOBAL_DATA':str(root/'data'),'CRUSH_GLOBAL_DATA':str(root/'data'),'XDG_DATA_HOME':str(root/'xdg-data'),'XDG_STATE_HOME':str(root/'state'),'PWD':str(root),'HARMONIKA_DISABLE_PROVIDER_AUTO_UPDATE':'1','OPENCODE_DISABLE_DEFAULT_PLUGINS':'true'}
  try:
   started=time.monotonic();r=subprocess.run([str(binary),'run','--auto','--standalone','--archived','--format','json','Jalankan tes fixture lokal dua tes, lalu laporkan hasil nyata.'],env=env,cwd=root,stdin=subprocess.DEVNULL,capture_output=True,timeout=80)
   events=[]
   for line in r.stdout.decode(errors='replace').splitlines():
    try:events.append(json.loads(line))
    except ValueError:pass
   failures=[e for e in events if e.get('type')=='error']
   passed=r.returncode==0 and not failures and any(x.get('has_result') and x.get('exit_code')==0 and x.get('status')=='completed' and x.get('truncated') is False and x.get('output_has_two_tests') for x in seen)
   report={'passed':passed,'native_exit_code':r.returncode,'latency_seconds':round(time.monotonic()-started,2),'requests':seen,'native_error_count':len(failures),'real_provider_called':False,'isolated_session':True}
   return report
  finally:server.shutdown();server.server_close();thread.join(timeout=2)
