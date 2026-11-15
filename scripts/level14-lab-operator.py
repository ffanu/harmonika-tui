"""Private fixture operator: one pinned disposable host and loopback coding API."""
import base64,importlib.util,json,re,time
from pathlib import Path
R=Path(Path('/tmp/harmonika-level14-root.path').read_text().strip());REPO=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('l14_transport',Path(__file__).with_name('level12-operator.py'));op=importlib.util.module_from_spec(spec);spec.loader.exec_module(op)
def sync():
 files=list((REPO/'backend/coding_runtime').glob('*.py'))+[REPO/'runtime/tools/engineering_evidence.py',REPO/'scripts/level14-lab-service.py']
 payload={str(p.relative_to(REPO)) if p.name!='level14-lab-service.py' else 'lab_service.py':base64.b64encode(p.read_bytes()).decode() for p in files}
 return op.python('''from pathlib import Path
import sys,json,base64,hashlib,socket
v=json.load(sys.stdin);r=Path('/opt/harmonika-exams/hl14-native/source')
s=socket.socket();busy=s.connect_ex(('127.0.0.1',18186))==0;s.close();assert not busy,'stop_owned_lab_service_before_sync'
for n,b in v.items():
 p=r/n;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(base64.b64decode(b));p.chmod(0o600)
print(json.dumps({'synced':len(v),'sha256':{n:hashlib.sha256((r/n).read_bytes()).hexdigest() for n in v if n.endswith(('agent.py','service.py','core.py','action_evidence.py'))}}))''',payload)
def start():
 return op.python('''import json,subprocess,socket,time,os
from pathlib import Path
r=Path('/opt/harmonika-exams/hl14-native');s=socket.socket();busy=s.connect_ex(('127.0.0.1',18186))==0;s.close();assert not busy,'lab_port_busy'
with (r/'service-output.txt').open('ab') as out:p=subprocess.Popen([str(r/'venv/bin/python'),str(r/'source/lab_service.py')],stdout=out,stderr=subprocess.STDOUT,start_new_session=True,env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1'})
print(json.dumps({'pid':p.pid,'started':True}))''')
def call(method,path,body=None,key=None,actor='api-token:14001',timeout=170):
 if not re.fullmatch(r'api-token:14[0-9]{3,10}',actor) or not path.startswith(('/workspaces','/jobs','/capabilities','/healthz','/__exam_shutdown')):raise RuntimeError('operator_scope')
 value={'method':method,'path':path,'body':body,'key':key,'actor':actor,'timeout':timeout}
 return op.python('''from pathlib import Path
import sys,json,urllib.request,urllib.error,time
v=json.load(sys.stdin);cfg=json.loads(Path('/opt/harmonika-exams/hl14-native/private-config.json').read_text());headers={'X-Harmonika-Coding-Secret':cfg['secret'],'X-Harmonika-Principal':v['actor'],'Content-Type':'application/json'}
if v['key']:headers['Idempotency-Key']=v['key']
req=urllib.request.Request('http://127.0.0.1:18186'+v['path'],data=json.dumps(v['body']).encode() if v['body'] is not None else None,headers=headers,method=v['method']);start=time.time()
try:
 with urllib.request.urlopen(req,timeout=v['timeout']) as res:status=res.status;raw=res.read(900000)
except urllib.error.HTTPError as e:status=e.code;raw=e.read(900000)
extra={}
if isinstance(v['body'],dict) and v['body'].get('stream') and status==200:
 text=raw.decode();events=[json.loads(line[6:]) for line in text.splitlines() if line.startswith('data: {')];final=next((e['data'] for e in reversed(events) if e.get('type')=='response.completed'),None)
 value=final if final is not None else {'status':'failed','error':next((e.get('data',{}).get('code','agent_failed') for e in reversed(events) if e.get('type')=='error'),'incomplete_sse'),'sse_error':True};extra={'sse_done':text.endswith('data: [DONE]\\n\\n'),'event_count':len(events)}
else:value=json.loads(raw)
print(json.dumps({'status':status,'value':value,'latency_ms':round((time.time()-start)*1000),**extra}))''',value,timeout=timeout+10)
def wait_ready():
 for _ in range(20):
  try:
   if call('GET','/healthz',timeout=5)['status']==200:return True
  except Exception:pass
  time.sleep(.5)
 raise RuntimeError('lab_not_ready')
def stop():
 result=call('POST','/__exam_shutdown',{})
 for _ in range(20):
  try:call('GET','/healthz',timeout=2)
  except Exception:return result
  time.sleep(.5)
 raise RuntimeError('lab_not_closed')
if __name__=='__main__':
 print(json.dumps(sync()));print(json.dumps(start()));wait_ready();v=call('GET','/capabilities');print(json.dumps({'api_status':v['status'],'ready':v['value'].get('ready'),'skill_count':len(v['value'].get('skills',[]))}))
