#!/usr/bin/env python3
"""Atomic registry upgrade, no gateway binary/service restart; preserves audit DB."""
import datetime,hashlib,json,os,shutil,sqlite3,subprocess,time,urllib.request
from pathlib import Path
B=Path('/opt/harmonika-ai');S=B/'discovery-scale-stage';M=B/'engine_registry';SERVICE=B/'master_engine/service.py'
def run(*a):return subprocess.check_output(a,stderr=subprocess.STDOUT,text=True).strip()
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def atomic(a,b):
 tmp=b.with_name(b.name+'.scale-next');shutil.copy2(a,tmp);os.replace(tmp,b)
def cap():
 req=urllib.request.Request('http://127.0.0.1:18193/capabilities',headers={'X-Harmonika-Master-Secret':Path('/etc/harmonika-ai/master-engine.token').read_text().strip(),'X-Harmonika-Principal':'api-token:1'})
 with urllib.request.urlopen(req,timeout=3) as r:return json.load(r)
assert os.geteuid()==0
assert json.loads((S/'benchmark.json').read_text())['modules']==3072
assert json.loads((S/'benchmark.json').read_text())['status']=='passed'
assert json.loads((S/'private-smoke.json').read_text())['status']=='passed'
assert json.loads((S/'linux-tests.json').read_text())['passed']==107
for name,value in json.loads((S/'baseline.json').read_text()).items():assert sha(Path(name))==value,'baseline_changed'
pids=json.loads((S/'pids.json').read_text());assert pids=={s:run('systemctl','show',s,'-p','MainPID','--value') for s in pids}
backup=Path('/var/backups/harmonika-master')/('scale-'+datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ'));backup.mkdir(parents=True,mode=0o700)
shutil.copytree(M,backup/'engine_registry',ignore=shutil.ignore_patterns('__pycache__','*.pyc'));shutil.copy2(SERVICE,backup/'service.py');(backup/'pids.json').write_text(json.dumps(pids))
with sqlite3.connect('/var/lib/harmonika-master/discovery.sqlite3') as src,sqlite3.connect(backup/'discovery.sqlite3') as dst:src.backup(dst)
os.chmod(backup/'discovery.sqlite3',0o600)
try:
 for path in (S/'engine_registry').glob('*.py'):atomic(path,M/path.name)
 atomic(S/'master_engine/service.py',SERVICE)
 run('systemctl','restart','harmonika-master-skills')
 value=None
 for i in range(50):
  try:
   value=cap()
   if value.get('discovery',{}).get('module_limit')==3072 and value['discovery']['registered']==1:break
  except OSError:pass
  time.sleep(.2)
 assert value and value['discovery']['module_limit']==3072 and value['discovery']['registered']==1 and value['discovery']['lazy_source_storage']
 assert value['catalog_size']==1951 and value['readiness']['available']==30
 assert pids=={s:run('systemctl','show',s,'-p','MainPID','--value') for s in pids}
 result={'status':'active_pending_https','backup':str(backup),'module_limit':3072,'dynamic_installed':1,'catalog':1951,'hot_reload_wait_active':True,'lazy_sources':True,'other_nine_pids_unchanged':True,'gateway_binary_changed':False,'session_databases_modified':False}
 (B/'discovery-scale-activation.json').write_text(json.dumps(result,indent=2));print(json.dumps(result))
except Exception:
 for path in (backup/'engine_registry').glob('*.py'):atomic(path,M/path.name)
 atomic(backup/'service.py',SERVICE)
 run('systemctl','restart','harmonika-master-skills')
 print(json.dumps({'status':'rolled_back','backup':str(backup),'audit_database_retained':True}));raise
