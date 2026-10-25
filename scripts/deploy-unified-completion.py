#!/usr/bin/env python3
"""Owner-run additive upgrade. Preserves encrypted production state; exact backups/rollback."""
import datetime,hashlib,json,os,shutil,sqlite3,subprocess,time,urllib.request
from pathlib import Path
assert os.geteuid()==0,'root_required'
BASE=Path('/opt/harmonika-ai');STAGE=BASE/'unified-completion-stage';MODULE=BASE/'unified_engine';WEB=Path('/srv/harmonika-web-chat/current').resolve();UNIT=Path('/etc/systemd/system/harmonika-unified.service');READY=Path('/var/lib/harmonika-unified/ready.json');DEPS=BASE/'unified-deps/graphql-3.2.13'
def run(*args):return subprocess.check_output(args,stderr=subprocess.STDOUT,text=True).strip()
def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def atomic(source,dest):
 tmp=dest.with_name(dest.name+'.completion-next');shutil.copy2(source,tmp);os.replace(tmp,dest)
assert json.loads((STAGE/'private-smoke.json').read_text())['status']=='passed'
for name,value in json.loads((STAGE/'gateway-baseline.json').read_text()).items():assert sha(WEB/name)==value,'gateway_changed:'+name
for name,value in json.loads((STAGE/'module-baseline.json').read_text()).items():assert sha(BASE/name)==value,'module_changed:'+name
db=sqlite3.connect('/var/lib/harmonika-unified/state.sqlite3');assert db.execute("SELECT count(*) FROM unified_jobs WHERE status IN ('queued','running')").fetchone()[0]==0,'wait_for_active_jobs';db.close()
other=['harmonika-ai','harmonika-shared-core','harmonika-coding-runtime','harmonika-telco-engine','harmonika-legal-engine','harmonika-legal-media','harmonika-web-builder'];pids={s:run('systemctl','show',s,'-p','MainPID','--value') for s in other}
backup=Path('/var/backups/harmonika-unified')/('completion-'+datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ'));backup.mkdir(parents=True,mode=0o700)
shutil.copytree(MODULE,backup/'module',ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
files=[(UNIT,backup/'unit.service'),(READY,backup/'ready.json')]+[(WEB/n,backup/('web-'+n)) for n in ('public_ai_unified.go','public_api_docs.go','harmonika-web')]+[(BASE/'master_engine'/n,backup/('master-'+n)) for n in ('index.py','unified_index.json')]
for source,dest in files:shutil.copy2(source,dest)
(backup/'pids.json').write_text(json.dumps(pids))
if not DEPS.exists():
 DEPS.parent.mkdir(exist_ok=True);shutil.copytree(STAGE/'deps',DEPS,ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
assert (DEPS/'graphql/__init__.py').is_file()
started=False
try:
 run('systemctl','stop','harmonika-unified')
 source=sqlite3.connect('/var/lib/harmonika-unified/state.sqlite3');dest=sqlite3.connect(backup/'state.sqlite3');source.backup(dest);dest.close();source.close();os.chmod(backup/'state.sqlite3',0o600)
 incoming=BASE/'unified_engine.completion-next';assert not incoming.exists();shutil.copytree(STAGE/'backend/unified_engine',incoming,ignore=shutil.ignore_patterns('__pycache__','*.pyc','*.go'))
 old=BASE/'unified_engine.completion-previous';assert not old.exists();os.replace(MODULE,old);os.replace(incoming,MODULE)
 atomic(MODULE/'deploy/harmonika-unified.service',UNIT);run('systemctl','daemon-reload');run('systemctl','start','harmonika-unified');started=True
 cap=None
 for _ in range(40):
  try:
   req=urllib.request.Request('http://127.0.0.1:18196/capabilities',headers={'X-Harmonika-Unified-Secret':Path('/etc/harmonika-ai/unified-engine.token').read_text().strip(),'X-Harmonika-Principal':'api-token:1'})
   with urllib.request.urlopen(req,timeout=2) as r:cap=json.load(r)
   break
  except OSError:time.sleep(.2)
 assert cap and cap['readiness']=={'available':57,'internal_only':12,'native_endpoint':1,'unavailable':0},'new_engine_not_ready'
 for n in ('index.py','unified_index.json'):atomic(STAGE/'backend/master_engine'/n,BASE/'master_engine'/n)
 run('systemctl','restart','harmonika-master-skills')
 for n in ('public_ai_unified.go','public_api_docs.go','harmonika-web'):atomic(STAGE/'gateway'/n,WEB/n)
 READY.write_text(json.dumps({'catalog_size':70,'completion':False,'completion_pending':True,'backup':str(backup)}));os.chmod(READY,0o600)
 run('systemctl','restart','harmonika-web-chat-live');time.sleep(1)
 for s in ('harmonika-unified','harmonika-master-skills','harmonika-web-chat-live'):assert run('systemctl','is-active',s)=='active'
 assert pids=={s:run('systemctl','show',s,'-p','MainPID','--value') for s in other}
 report={'status':'active_pending_https_new_discovery_flags_disabled','backup':str(backup),'readiness':cap['readiness'],'other_seven_pids_unchanged':True,'encrypted_state_preserved':True,'keys_rotated':False,'tui_modified':False,'dependency_isolated':str(DEPS)}
 (BASE/'unified-completion-activation.json').write_text(json.dumps(report,indent=2));print(json.dumps(report))
except Exception:
 subprocess.run(['systemctl','stop','harmonika-unified'],capture_output=True)
 old=BASE/'unified_engine.completion-previous'
 if old.exists():
  if MODULE.exists():os.replace(MODULE,BASE/'unified_engine.completion-failed')
  os.replace(old,MODULE)
 for dest,source in files:atomic(source,dest)
 run('systemctl','daemon-reload');run('systemctl','restart','harmonika-unified');run('systemctl','restart','harmonika-master-skills');run('systemctl','restart','harmonika-web-chat-live')
 print(json.dumps({'status':'rolled_back','backup':str(backup),'production_database_retained':True}));raise
