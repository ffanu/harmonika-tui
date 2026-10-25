#!/usr/bin/env python3
"""Owner-run activation, hash preconditions, exact backups and bounded rollback."""
import datetime,hashlib,json,os,shutil,subprocess,time,urllib.request
from pathlib import Path
assert os.geteuid()==0,'root_required'
BASE=Path('/opt/harmonika-ai');STAGE=BASE/'core-module-stage';GO=BASE/'core-gateway-stage'
WEB=Path('/srv/harmonika-web-chat/current').resolve();CORE=BASE/'core_engine'
UNIT=Path('/etc/systemd/system/harmonika-shared-core.service');STATE=Path('/var/lib/harmonika-core')
def run(*args):return subprocess.run(args,check=True,capture_output=True,text=True).stdout.strip()
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def atomic(src,dst):
 tmp=dst.with_name(dst.name+'.core-new');shutil.copy2(src,tmp);os.replace(tmp,dst)
def pid(name):return run('systemctl','show',name,'-p','MainPID','--value')
others=['harmonika-ai','harmonika-coding-runtime','harmonika-telco-engine','harmonika-legal-engine','harmonika-legal-media','harmonika-web-builder']
before={s:pid(s) for s in others}
smoke=json.loads((BASE/'core-private-smoke.json').read_text())
assert smoke['local_operations_tested']==12 and smoke['unavailable_and_local_only_rejected']==66
assert not CORE.exists() and not UNIT.exists(),'already_installed'
for name,digest in json.loads((GO/'baseline.json').read_text()).items():assert sha(WEB/name)==digest,'gateway_changed:'+name
for name,digest in json.loads((STAGE/'master-baseline.json').read_text()).items():assert sha(BASE/'master_engine'/name)==digest,'master_changed:'+name
assert not (BASE/'master_engine/core_index.json').exists()
assert not (WEB/'public_ai_core.go').exists()
assert not STATE.exists(),'unexpected_existing_state'
BACK=Path('/var/backups/harmonika-core')/datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')
BACK.mkdir(parents=True,mode=0o700);os.chmod(BACK,0o700)
changed=[(WEB/n,BACK/('web-'+n)) for n in ['main.go','public_api_docs.go','public_ai_master.go','harmonika-web']]+[(BASE/'master_engine'/n,BACK/('master-'+n)) for n in ['index.py','service.py']]
for src,dst in changed:shutil.copy2(src,dst)
(BACK/'baseline.json').write_text(json.dumps({'web':str(WEB),'other_pids':before,'files':{str(p):sha(p) for p,_ in changed}},indent=2))
mutated=False
try:
 STATE.mkdir(mode=0o700);shutil.copytree(STAGE/'backend/core_engine',CORE,ignore=shutil.ignore_patterns('__pycache__','*.pyc','*.go'))
 shutil.copy2(CORE/'deploy/harmonika-shared-core.service',UNIT)
 run('systemctl','daemon-reload');run('systemctl','enable','--now','harmonika-shared-core')
 caps=None
 for _ in range(30):
  try:
   req=urllib.request.Request('http://127.0.0.1:18195/capabilities',headers={'X-Harmonika-Core-Secret':Path('/etc/harmonika-ai/core-engine.token').read_text().strip(),'X-Harmonika-Principal':'api-token:1'})
   with urllib.request.urlopen(req,timeout=2) as r:caps=json.load(r)
   break
  except OSError:time.sleep(.3)
 assert caps and caps['catalog_size']==99 and caps['readiness']['available']==12,'private_core_not_ready'
 mutated=True
 for n in ['index.py','service.py','core_index.json']:atomic(STAGE/'backend/master_engine'/n,BASE/'master_engine'/n)
 run('systemctl','restart','harmonika-master-skills')
 ready={'catalog_size':99,'readiness':caps['readiness'],'activation_backup':str(BACK)}
 (STATE/'ready.json').write_text(json.dumps(ready));os.chmod(STATE/'ready.json',0o600)
 for n in ['main.go','public_api_docs.go','public_ai_master.go','public_ai_core.go','harmonika-web']:atomic(GO/n,WEB/n)
 run('systemctl','restart','harmonika-web-chat-live')
 time.sleep(2)
 for name in ['harmonika-shared-core','harmonika-master-skills','harmonika-web-chat-live']:assert run('systemctl','is-active',name)=='active'
 assert before=={s:pid(s) for s in others},'unrelated_service_changed'
 report={**ready,'status':'active_pending_https_acceptance','other_service_pids_unchanged':True,'services_restarted':['harmonika-master-skills','harmonika-web-chat-live'],'service_added':'harmonika-shared-core','production_state_from_fixtures':False}
 (BASE/'core-activation.json').write_text(json.dumps(report,indent=2));print(json.dumps(report))
except Exception:
 for dst,src in changed:atomic(src,dst)
 (WEB/'public_ai_core.go').unlink(missing_ok=True);(BASE/'master_engine/core_index.json').unlink(missing_ok=True);(STATE/'ready.json').unlink(missing_ok=True)
 if mutated:
  run('systemctl','restart','harmonika-master-skills');run('systemctl','restart','harmonika-web-chat-live')
 subprocess.run(['systemctl','disable','--now','harmonika-shared-core'],capture_output=True)
 print(json.dumps({'status':'rolled_back','backup':str(BACK),'encrypted_state_retained':True}))
 raise
