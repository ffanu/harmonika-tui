#!/usr/bin/env python3
"""Owner-run acceptance with exact disposable plugin fixtures and token stdin."""
import hashlib,json,os,shutil,subprocess,sys,tempfile,time,urllib.error,urllib.request,uuid
from pathlib import Path
PRIVATE=os.environ.get('HARMONIKA_DISCOVERY_PRIVATE')=='1'
STAGE=Path('/opt/harmonika-ai/discovery-scale-stage');ROOT=(STAGE if PRIVATE else Path('/opt/harmonika-ai'))/'skills-runtime'
SOURCE=(STAGE/'engine_registry/skills/text-metrics/skill.py' if PRIVATE else Path('/opt/harmonika-ai/engine_registry/skills/text-metrics/skill.py')).read_text()
BASE='http://127.0.0.1:18197' if PRIVATE else 'https://chat.harmonika.id/v1/skills'
PREFIX='zz-discovery-probe-';paths=[]
class NoRedirect(urllib.request.HTTPRedirectHandler):
 def redirect_request(self,*args,**kwargs):raise RuntimeError('redirect_refused')
op=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect())
def main():
 auth={} if PRIVATE else json.load(sys.stdin)
 headers={'X-Harmonika-Master-Secret':Path('/etc/harmonika-ai/master-engine.token').read_text().strip(),'X-Harmonika-Principal':'api-token:900000000000777051'} if PRIVATE else {'Authorization':'Bearer '+auth['token']}
 def route(path):
  if not PRIVATE:return path
  if path=='/discovery':return '/skills/discovery'
  if path.startswith('/text.') or path.startswith('/diagnostic.'):return '/skills'+path
  if path.startswith('?'):return '/skills'+path
  return path
 def call(method,path,body=None,authorized=True):
  req=urllib.request.Request(BASE+route(path),method=method,data=json.dumps(body).encode() if body is not None else None,headers={**(headers if authorized else {}),'Content-Type':'application/json','Idempotency-Key':str(uuid.uuid4())})
  try:
   with op.open(req,timeout=20) as r:return r.status,json.loads(r.read(1048577))
  except urllib.error.HTTPError as e:return e.code,json.loads(e.read(65536))
 def wait(test):
  deadline=time.monotonic()+40
  while time.monotonic()<deadline:
   code,value=call('GET','/discovery')
   if code==200 and test(value):return value
   time.sleep(.4)
  raise AssertionError('discovery_convergence_timeout')
 def fixture(name,source):
  target=ROOT/(PREFIX+name);assert not target.exists(),'fixture_already_exists'
  with tempfile.TemporaryDirectory(prefix='harmonika-discovery-prepare-',dir=str(ROOT.parent)) as temp:
   p=Path(temp)/'plugin';p.mkdir(mode=0o700);(p/'skill.py').write_text(source);(p/'manifest.json').write_text(json.dumps({'module_sha256':hashlib.sha256(source.encode()).hexdigest()}));os.replace(p,target)
  paths.append(target);return target
 pids={s:subprocess.check_output(['systemctl','show',s,'-p','MainPID','--value'],text=True).strip() for s in ('harmonika-master-skills','harmonika-web-chat-live')}
 results=[]
 try:
  for attempt in range(30 if PRIVATE else 1):
   try:caps_code,caps=call('GET','/capabilities');break
   except urllib.error.URLError:
    if not PRIVATE or attempt==29:raise
    time.sleep(.1)
  assert caps_code==200 and caps['features']['runtime_discovery'] and caps['features']['hot_skill_registration'];assert caps['discovery']['registered']==1 and caps['discovery']['module_limit']==3072 and caps['discovery']['lazy_source_storage'] is True
  assert call('GET','/discovery',authorized=False)[0] in (401,403)
  code,data=call('POST','/invoke',{'skill_id':'text.metrics','input':{'text':'Halo Kak!\nInternet Harmonika'}});assert code==200 and data['result']['words']==4 and data['result']['lines']==2;results.append('real_skill')
  assert call('POST','/invoke',{'skill_id':'text.metrics','input':{'text':4}})[0]==400;results.append('strict_payload')
  hot=fixture('hot',SOURCE.replace('text.metrics','diagnostic.discovery_probe'));wait(lambda s:s['registered']==2)
  code,data=call('POST','/invoke',{'skill_id':'diagnostic.discovery_probe','input':{'text':'safe fixture'}});assert code==200 and data['result']['words']==2;results.append('hot_register_and_invoke')
  # Change bytes without operator updating approval: next scan must withdraw it.
  (hot/'skill.py').write_text(SOURCE.replace('text.metrics','diagnostic.discovery_probe')+'\n# unapproved change\n');wait(lambda s:s['registered']==1 and s['error_codes'].get('E_DIGEST_MISMATCH')==1)
  assert call('POST','/invoke',{'skill_id':'diagnostic.discovery_probe','input':{'text':'safe'}})[0]==404;results.append('tamper_withdrawal')
  shutil.rmtree(hot);wait(lambda s:s['registered']==1 and s['rejected']==0)
  fixture('duplicate-a',SOURCE.replace('text.metrics','diagnostic.duplicate_probe'));fixture('duplicate-b',SOURCE.replace('text.metrics','diagnostic.duplicate_probe'));fixture('broken','def (:\n');fixture('reserved',SOURCE.replace('text.metrics','core.retrieve'))
  value=wait(lambda s:s['rejected']==4)
  assert value['error_codes']=={'E_DUPLICATE_ID':2,'E_SYNTAX':1,'E_RESERVED_ID':1};assert value['registered']==1
  assert call('POST','/invoke',{'skill_id':'diagnostic.duplicate_probe','input':{'text':'safe'}})[0]==404;results.append('broken_duplicate_and_reserved_isolation')
  assert pids=={s:subprocess.check_output(['systemctl','show',s,'-p','MainPID','--value'],text=True).strip() for s in pids};results.append('no_gateway_or_master_restart_during_hot_changes')
 finally:
  for p in paths:
   if p.parent==ROOT and p.name.startswith(PREFIX) and p.exists():shutil.rmtree(p)
  final=wait(lambda s:s['registered']==1 and s['rejected']==0)
 report={'status':'passed','private':PRIVATE,'scenarios':results,'final_registered':final['registered'],'fixture_directories_removed':True,'gateway_binary_changed':False}
 target=STAGE/'private-smoke.json' if PRIVATE else Path('/opt/harmonika-ai/discovery-scale-public-smoke.json');target.write_text(json.dumps(report,indent=2));print(json.dumps(report))
if __name__=='__main__':
 try:main()
 except Exception as e:print(json.dumps({'status':'failed','error_type':type(e).__name__}),flush=True);raise SystemExit(1)
