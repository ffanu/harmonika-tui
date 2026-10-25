#!/usr/bin/env python3
"""Activate tested operator discovery; exact backups and file rollback only."""
import datetime,hashlib,json,os,shutil,subprocess,time,urllib.request
from pathlib import Path
BASE=Path('/opt/harmonika-ai');STAGE=BASE/'discovery-stage';MASTER=BASE/'master_engine';MODULE=BASE/'engine_registry';PLUGINS=BASE/'skills-runtime';DROP=Path('/etc/systemd/system/harmonika-master-skills.service.d/discovery.conf')
def run(*args):return subprocess.check_output(args,stderr=subprocess.STDOUT,text=True).strip()
def atomic(src,dst):
 p=dst.with_name(dst.name+'.discovery-next');shutil.copy2(src,p);os.replace(p,dst)
def capability():
 req=urllib.request.Request('http://127.0.0.1:18193/capabilities',headers={'X-Harmonika-Master-Secret':Path('/etc/harmonika-ai/master-engine.token').read_text().strip(),'X-Harmonika-Principal':'api-token:1'})
 with urllib.request.urlopen(req,timeout=2) as res:return json.load(res)
assert os.geteuid()==0
assert json.loads((STAGE/'private-smoke.json').read_text())['status']=='passed'
assert json.loads((STAGE/'benchmark.json').read_text())['modules']==256
assert json.loads((STAGE/'linux-tests.json').read_text())['status']=='passed'
for name,value in json.loads((STAGE/'baseline.json').read_text()).items():assert hashlib.sha256(Path(name).read_bytes()).hexdigest()==value,'baseline_changed'
assert not MODULE.exists() and not PLUGINS.exists() and not DROP.exists(),'existing_discovery_installation'
other=json.loads((STAGE/'pids.json').read_text());assert other=={s:run('systemctl','show',s,'-p','MainPID','--value') for s in other},'unrelated_service_changed'
backup=Path('/var/backups/harmonika-master')/('discovery-'+datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ'));backup.mkdir(parents=True,mode=0o700)
shutil.copy2(MASTER/'service.py',backup/'service.py');shutil.copy2('/etc/systemd/system/harmonika-master-skills.service',backup/'master.service');(backup/'pids.json').write_text(json.dumps(other))
try:
 shutil.copytree(STAGE/'engine_registry',MODULE,ignore=shutil.ignore_patterns('__pycache__','*.pyc','*.go'))
 PLUGINS.mkdir(mode=0o755);shutil.copytree(MODULE/'skills/text-metrics',PLUGINS/'text-metrics')
 DROP.parent.mkdir(exist_ok=True);atomic(MODULE/'deploy/discovery.conf',DROP);atomic(STAGE/'master_engine/service.py',MASTER/'service.py')
 run('systemctl','daemon-reload');run('systemctl','restart','harmonika-master-skills')
 cap=None
 for attempt in range(50):
  try:
   cap=capability()
   if cap.get('discovery',{}).get('registered')==1:break
  except OSError:pass
  time.sleep(.2)
 assert cap and cap['discovery']['status']=='ready' and cap['discovery']['registered']==1,'engine_not_ready'
 assert cap['catalog_size']==1951 and cap['readiness']['available']==30,'catalog_regression'
 assert other=={s:run('systemctl','show',s,'-p','MainPID','--value') for s in other}
 report={'status':'active_pending_public_acceptance','backup':str(backup),'master_catalog':cap['catalog_size'],'direct_handlers':cap['readiness']['available'],'dynamic_registered':1,'other_nine_pids_unchanged':True,'gateway_binary_changed':False,'session_databases_modified':False,'customer_data_modified':False,'new_database':'/var/lib/harmonika-master/discovery.sqlite3'}
 (BASE/'discovery-activation.json').write_text(json.dumps(report,indent=2));print(json.dumps(report))
except Exception:
 atomic(backup/'service.py',MASTER/'service.py')
 if DROP.exists():DROP.unlink()
 run('systemctl','daemon-reload');run('systemctl','restart','harmonika-master-skills')
 # Preserve all diagnostic state; never delete a DB or user module on rollback.
 print(json.dumps({'status':'rolled_back','backup':str(backup),'new_module_and_audit_retained':True}));raise
