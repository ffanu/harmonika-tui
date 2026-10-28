import importlib.util,json,os,tempfile,time,io,sys,shlex,urllib.request
from pathlib import Path
root=Path(__file__).resolve().parents[1]
# Capture only technical tool metadata. Never persist prompts, keys, or responses.
live='--live' in sys.argv
candidate=Path(sys.argv[sys.argv.index('--candidate')+1]).resolve() if '--candidate' in sys.argv else None
spec=importlib.util.spec_from_file_location('native',root/'scripts/smoke-native-interaction.py');n=importlib.util.module_from_spec(spec);spec.loader.exec_module(n)
class ObservedStream:
 def __init__(self,response,owner):self.response=response;self.owner=owner;self.pending=b''
 def close(self):self.response.close()
 def read1(self,n):
  raw=self.response.read1(n);self.pending+=raw
  while b'\n' in self.pending:
   line,self.pending=self.pending.split(b'\n',1)
   if not line.startswith(b'data:'):continue
   payload=line[5:].strip()
   if payload==b'[DONE]':self.owner.stream_completed=True;continue
   try:
    for choice in json.loads(payload).get('choices',[]):
     text=choice.get('delta',{}).get('content','')
     if isinstance(text,str):self.owner.answer_chars+=len(text)
   except (ValueError,AttributeError):pass
  if len(self.pending)>65536:self.pending=b''
  return raw
class Capture(n.GatewayFixture):
 def _upstream(self,req):
  b=json.loads(req.data); defs=b.get('tools',[])
  self.tool_sets.append([t.get('function',{}).get('name','') for t in defs])
  if live:
   headers={'Content-Type':'application/json','Authorization':'Bearer '+(Path.home()/'.config/harmonika/harmonika-api-key').read_text().strip()}
   headers.update(active.get('providers',{}).get('harmonika',{}).get('extra_headers',{}))
   response=urllib.request.urlopen(urllib.request.Request('https://chat.harmonika.id/v1/chat/completions',data=req.data,headers=headers),timeout=60)
   self.http_statuses.append(response.status)
   return ObservedStream(response,self) if b'"stream": true' in req.data or b'"stream":true' in req.data else response
  return super()._upstream(req)
g=Capture();g.tool_sets=[];g.http_statuses=[];g.stream_completed=False;g.answer_chars=0;g.start();session=None
base=Path(tempfile.mkdtemp(prefix='harmonika-tool-capture-'));config=base/'config';config.mkdir(mode=0o700);data=base/'data';data.mkdir();project=base/'project';project.mkdir()
active=json.loads((Path.home()/'.config/harmonika/harmonika.json').read_text())
cfg={k:active[k] for k in ('mcp','lsp','options')};cfg=json.loads(json.dumps(cfg));cfg['options'].update(context_paths=[],global_context_paths=[],disable_metrics=True)
if candidate:cfg['mcp']['harmonika']['args']=[str(candidate/'tools/compact-core-mcp.cjs')]
cfg['permissions']={};cfg['models']={x:{'provider':'harmonika','model':'harmonika-ai'} for x in ('large','small')}
cfg['providers']={'harmonika':{'name':'Harmonika Ai','api_key':'fixture-token','base_url':f'http://127.0.0.1:{g.port}/v1','models':[{'id':'harmonika-ai','name':'Harmonika Ai','context_window':32768,'max_tokens':8192}]}}
for name in ('harmonika.json','crush.json'):(config/name).write_text(json.dumps(cfg));(config/name).chmod(0o600)
env={k:v for k,v in os.environ.items() if not k.startswith(('HARMONIKA_','CRUSH_'))};env.update(HARMONIKA_GLOBAL_CONFIG=str(config),HARMONIKA_GLOBAL_DATA=str(data),TERM='xterm-256color',LANG='en_US.UTF-8')
binary=(Path.home()/'.config/harmonika-terminal/current/harmonika').resolve()
entry=[sys.executable,'-I',str(Path.home()/'.config/harmonika-terminal/current/tools/workbench.py'),'start','--offline','--binary',str(binary),'--config',str(config),'--data',str(data),'--']
wrapper=base/'start';wrapper.write_text('#!/bin/sh\nexec '+shlex.join(entry)+' "$@"\n');wrapper.chmod(0o700);n.LAUNCHER=wrapper
try:
 session=n.TuiSession(env,str(project),['--cwd',str(project),'--data-dir',str(data)],42,150);session.pump(12)
 session.send('hello');session.send('\r');session.wait_until(lambda:bool(g.tool_sets),25);session.pump(1)
 if live:session.wait_until(lambda:g.stream_completed and g.answer_chars>0,65);session.pump(4)
 else:session.wait_until(lambda:n.MARK+'-L' in session.term.visible(),15)
 visible=session.term.visible()
 out={'ok':bool(g.tool_sets) and all(len(x)<=32 for x in g.tool_sets) and (g.stream_completed and g.answer_chars>0 if live else n.MARK+'-L' in visible) and 'Bad Request' not in visible,'stream_completed':g.stream_completed,'answer_char_count':g.answer_chars,'live':live,'http_statuses':g.http_statuses,'reply_rendered':n.MARK+'-L' in visible if not live else g.stream_completed and g.answer_chars>0 and 'Bad Request' not in visible,'captured':bool(g.tool_sets),'tool_counts':[len(x) for x in g.tool_sets],'tool_names':g.tool_sets,'schema_source':'real native PTY + active MCP/LSP config','private_content_logged':False}
 Path(root/'docs'/('TUI-TOOLS-LIVE-SMOKE.json' if live else 'TUI-TOOLS-FULL-CONFIG-SMOKE.json')).write_text(json.dumps(out,indent=2));print(json.dumps(out,indent=2));status=0 if out['ok'] else 1
finally:
 try:
  if session:session.send(b'\x03');session.pump(1);session.send(b'\x03');session.pump(1)
 finally:g.stop()

sys.exit(status)
