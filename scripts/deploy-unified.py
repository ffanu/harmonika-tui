#!/usr/bin/env python3
"""Initial owner-controlled activation: baseline hashes, backups, isolated state, rollback."""
import datetime,hashlib,json,os,shutil,subprocess,time,urllib.request
from pathlib import Path
assert os.geteuid()==0
BASE=Path('/opt/harmonika-ai');STAGE=BASE/'unified-stage';WEB=Path('/srv/harmonika-web-chat/current').resolve();ROOT=BASE/'unified_engine';STATE=Path('/var/lib/harmonika-unified');UNIT=Path('/etc/systemd/system/harmonika-unified.service')
def run(*cmd):return subprocess.check_output(cmd,stderr=subprocess.STDOUT,text=True).strip()
def digest(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def copy(src,dst):
 tmp=dst.with_name(dst.name+'.unified-new');shutil.copy2(src,tmp);os.replace(tmp,dst)
assert json.loads((STAGE/'private-smoke.json').read_text())['status']=='passed'
assert not ROOT.exists() and not STATE.exists() and not UNIT.exists()
for n,h in json.loads((STAGE/'gateway-baseline.json').read_text()).items():assert digest(WEB/n)==h,'gateway_source_changed:'+n
for n,h in json.loads((STAGE/'master-baseline.json').read_text()).items():assert digest(BASE/'master_engine'/n)==h,'master_source_changed:'+n
assert not (BASE/'master_engine/unified_index.json').exists() and not (WEB/'public_ai_unified.go').exists()
others=['harmonika-ai','harmonika-shared-core','harmonika-coding-runtime','harmonika-telco-engine','harmonika-legal-engine','harmonika-legal-media','harmonika-web-builder'];pids={s:run('systemctl','show',s,'-p','MainPID','--value') for s in others}
BACK=Path('/var/backups/harmonika-unified')/datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ');BACK.mkdir(parents=True,mode=0o700)
files=[(WEB/n,BACK/('web-'+n)) for n in ['main.go','public_ai_master.go','public_api_docs.go','harmonika-web']]+[(BASE/'master_engine'/n,BACK/('master-'+n)) for n in ['index.py','service.py']]
for a,b in files:shutil.copy2(a,b)
(BACK/'pids.json').write_text(json.dumps(pids));changed=False
try:
 STATE.mkdir(mode=0o700);shutil.copytree(STAGE/'backend/unified_engine',ROOT,ignore=shutil.ignore_patterns('__pycache__','*.pyc','*.go'));shutil.copy2(ROOT/'deploy/harmonika-unified.service',UNIT);run('systemctl','daemon-reload');run('systemctl','enable','--now','harmonika-unified')
 caps=None
 for _ in range(30):
  try:
   req=urllib.request.Request('http://127.0.0.1:18196/capabilities',headers={'X-Harmonika-Unified-Secret':Path('/etc/harmonika-ai/unified-engine.token').read_text().strip(),'X-Harmonika-Principal':'api-token:1'})
   with urllib.request.urlopen(req,timeout=2) as r:caps=json.load(r)
   break
  except OSError:time.sleep(.2)
 assert caps and caps['catalog_size']==70 and caps['readiness']['available']==53
 changed=True
 for n in ['index.py','service.py','unified_index.json']:copy(STAGE/'backend/master_engine'/n,BASE/'master_engine'/n)
 run('systemctl','restart','harmonika-master-skills')
 (STATE/'ready.json').write_text(json.dumps({'catalog_size':70,'backup':str(BACK)}));os.chmod(STATE/'ready.json',0o600)
 for n in ['main.go','public_ai_master.go','public_api_docs.go','public_ai_unified.go','harmonika-web']:copy(STAGE/'gateway'/n,WEB/n)
 run('systemctl','restart','harmonika-web-chat-live');time.sleep(2)
 for s in ['harmonika-unified','harmonika-master-skills','harmonika-web-chat-live']:assert run('systemctl','is-active',s)=='active'
 assert pids=={s:run('systemctl','show',s,'-p','MainPID','--value') for s in others}
 report={'status':'active_pending_https','backup':str(BACK),'readiness':caps['readiness'],'other_seven_pids_unchanged':True,'tui_modified':False,'production_state_copied_from_staging':False}
 (BASE/'unified-activation.json').write_text(json.dumps(report,indent=2));print(json.dumps(report))
except Exception:
 for dst,src in files:copy(src,dst)
 (WEB/'public_ai_unified.go').unlink(missing_ok=True);(BASE/'master_engine/unified_index.json').unlink(missing_ok=True);(STATE/'ready.json').unlink(missing_ok=True)
 if changed:
  run('systemctl','restart','harmonika-master-skills');run('systemctl','restart','harmonika-web-chat-live')
 subprocess.run(['systemctl','disable','--now','harmonika-unified'],capture_output=True)
 print(json.dumps({'status':'rolled_back','backup':str(BACK),'encrypted_state_retained':True}));raise
