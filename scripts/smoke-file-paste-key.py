"""Real Ctrl+V test through the terminal workbench using copied Finder references.
Clipboard is read-only. File contents and paths are not logged; model is loopback.
"""
import importlib.util,json,os,tempfile,shlex,sys,subprocess,shutil
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('native',ROOT/'scripts/smoke-native-interaction.py');n=importlib.util.module_from_spec(spec);spec.loader.exec_module(n)
PASTES=['Harmonika paste: baris pertama\nBaris kedua — café ✓', '```python\n'+''.join('print("baris-%03d")\n'%i for i in range(100))+'```']
class Fixture(n.GatewayFixture):
 def _upstream(self,req):
  body=json.loads(req.data)
  for m in body.get('messages',[]):
   if m.get('role')=='user':
    text=m.get('content','')
    if isinstance(text,list):text=''.join(x.get('text','') for x in text if isinstance(x,dict))
    for i,paste in enumerate(PASTES):
     if paste in text:self.matched.add(i)
  return super()._upstream(req)
g=Fixture();g.matched=set();g.start();base=Path(tempfile.mkdtemp(prefix='harmonika-paste-qa-'));config=base/'config';config.mkdir(mode=0o700);project=base/'project';project.mkdir();data=base/'data';data.mkdir()
cfg={'models':{x:{'model':'harmonika-ai','provider':'harmonika'} for x in ['large','small']},'providers':{'harmonika':{'name':'Harmonika Ai','api_key':'fixture-token','base_url':f'http://127.0.0.1:{g.port}/v1','models':[{'id':'harmonika-ai','name':'Harmonika Ai','context_window':32768,'max_tokens':8192}]}},'mcp':{},'lsp':{},'options':{'disable_provider_auto_update':True,'disable_metrics':True,'context_paths':[],'global_context_paths':[],'tui':{'mouse':True}},'permissions':{}}
for name in ['harmonika.json','crush.json']:(config/name).write_text(json.dumps(cfg));(config/name).chmod(0o600)
current=Path(os.environ.get('HARMONIKA_PASTE_CANDIDATE',str(Path.home()/'.config/harmonika-terminal/current')));wrapper=base/'start';wrapper.write_text('#!/bin/sh\nexec '+shlex.join([sys.executable,'-I',str(current/'tools/workbench.py'),'start','--offline','--binary',str(Path.home()/'.config/harmonika-terminal/current/harmonika'),'--config',str(config),'--data',str(data),'--'])+' "$@"\n');wrapper.chmod(0o700);n.LAUNCHER=wrapper
session=None;checks=[]
f=base/'gambar café (1).png';f.touch()
sys.path.insert(0,str(current/'tools'))
from file_paste import paste_text,READ_JXA
clip=subprocess.run(['/usr/bin/osascript','-l','JavaScript','-e',READ_JXA],capture_output=True,text=True,check=True)
v=json.loads(clip.stdout)
if not v.get('paths'):raise SystemExit('No copied Finder file; no clipboard content printed')
PASTES.insert(0,paste_text(v))

env={k:v for k,v in os.environ.items() if not k.startswith(('HARMONIKA_','CRUSH_'))};env.update(HARMONIKA_GLOBAL_CONFIG=str(config),HARMONIKA_GLOBAL_DATA=str(data),TERM='xterm-256color',LANG='en_US.UTF-8')
try:
 session=n.TuiSession(env,str(project),[],42,150);session.pump(6)
 for index,text in enumerate(PASTES):
  count=len(g.requests)
  if index==0:
   context=json.loads(next((config/'workbench-state').glob('instance-*/context.json')).read_text())
   session.send(b'\x16')
  else:session.send('\x1b[200~'+text+'\x1b[201~')
  session.pump(5)
  if index==0: print(json.dumps({'key_diagnostic':{'path_visible':text in session.term.visible(),'clipboard_error':'Clipboard belum' in session.term.visible(),'pane_error':'kolom chat' in session.term.visible(),'onboarding':'initialize this project' in session.term.visible()}}),flush=True)
  not_sent=len(g.requests)==count
  session.send('\r');matched=session.wait_until(lambda:index in g.matched,20);session.pump(5)
  checks.append({'case':['finder_path','unicode_multiline','long_code_100_lines'][index],'preserved_exactly':matched,'paste_did_not_autosend':not_sent})
 report={'ok':all(c['preserved_exactly'] and c['paste_did_not_autosend'] for c in checks),'checks':checks,'clipboard_access':'read-only Finder references; contents not read or logged','gateway':'local fixture only','existing_sessions_touched':False}
 (ROOT/'docs/verification/tool-budget-20261101/file-paste-key-report.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report,indent=2))
finally:
 if session:session.send(b'\x03');session.pump(1);session.send(b'\x03');session.pump(1)
 g.stop()
 shutil.rmtree(base)

raise SystemExit(0 if report['ok'] else 1)
