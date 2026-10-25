#!/usr/bin/env python3
"""Enable private vision only after the Windows worker has passed qualification."""
from contextlib import closing
import datetime,json,os,shutil,sqlite3,subprocess,time,urllib.request
from pathlib import Path
B=Path('/opt/harmonika-ai');S=B/'rtx-vision-stage';Q=B/'rtx-queue/backend/rtx_queue';CONFIG=Path('/etc/harmonika-ai/rtx-queue/config.json');DROP=Path('/etc/systemd/system/harmonika-rtx-queue.service.d/vision.conf')
def run(*args):return subprocess.check_output(args,stderr=subprocess.STDOUT,text=True).strip()
def get(url,token):
 with urllib.request.urlopen(urllib.request.Request(url,headers={'Authorization':'Bearer '+token}),timeout=10) as r:return json.load(r)
assert os.geteuid()==0
assert json.loads((S/'local-vision-smoke.json').read_text())['status']=='passed'
assert json.loads((S/'private-worker-smoke.json').read_text())['status']=='passed'
worker_token=Path('/etc/harmonika-ai/rtx-vision-worker.token').read_text().strip()
assert get('http://127.0.0.1:18189/v1/rtx/health',worker_token)['capabilities']['analyze'] is True
assert not DROP.exists() and 'vision_endpoint' not in json.loads(CONFIG.read_text()),'vision_already_configured'
query="import sqlite3; c=sqlite3.connect('/var/lib/harmonika-rtx-queue/queue.sqlite3');print(c.execute(\"SELECT count(*) FROM jobs WHERE status NOT IN ('completed','failed','cancelled')\").fetchone()[0]);c.close()"
assert run('runuser','-u','harmonika-rtx-queue','--','/usr/bin/python3','-c',query)=='0','queue_not_idle'
services=['harmonika-ai','harmonika-web-chat-live','harmonika-shared-core','harmonika-unified','harmonika-master-skills','harmonika-rtx-tunnel','harmonika-rtx-vision-tunnel']
pids={s:run('systemctl','show',s,'-p','MainPID','--value') for s in services}
backup=Path('/var/backups/harmonika-media')/('vision-'+datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ'));backup.mkdir(parents=True,mode=0o700)
shutil.copy2(Q/'service.py',backup/'service.py');shutil.copy2(CONFIG,backup/'config.json')
try:
 run('systemctl','stop','harmonika-rtx-queue')
 with closing(sqlite3.connect('/var/lib/harmonika-rtx-queue/queue.sqlite3')) as src,closing(sqlite3.connect(backup/'queue.sqlite3')) as dst:src.backup(dst);assert dst.execute('PRAGMA quick_check').fetchone()[0]=='ok'
 os.chmod(backup/'queue.sqlite3',0o600)
 for name in ('service.py','router.py'):
  tmp=Q/(name+'.vision-next');shutil.copy2(S/'queue'/name,tmp);os.replace(tmp,Q/name)
 config=json.loads(CONFIG.read_text());config['vision_endpoint']='http://127.0.0.1:18189'
 tmp=CONFIG.with_name('config.json.vision-next');tmp.write_text(json.dumps(config));tmp.chmod(0o600);os.replace(tmp,CONFIG)
 DROP.write_text('[Service]\nLoadCredential=vision.token:/etc/harmonika-ai/rtx-vision-worker.token\n')
 run('systemctl','daemon-reload');run('systemctl','start','harmonika-rtx-queue')
 token=Path('/etc/harmonika-ai/rtx-queue-client.token').read_text().strip();health=None
 for _ in range(30):
  try:
   health=get('http://127.0.0.1:18186/health',token)
   if health['worker']['analyze'] and health['worker']['generate'] and health['queue_ready']:break
  except OSError:pass
  time.sleep(.3)
 assert health and health['worker']['analyze'] and health['worker']['generate'] and health['worker']['vision_local_only'] and health['queue_ready']
 assert pids=={s:run('systemctl','show',s,'-p','MainPID','--value') for s in services}
 report={'status':'active_pending_https_and_tui_smoke','backup':str(backup),'worker':health['worker'],'queue_journal':'WAL retained','only_restarted':['harmonika-rtx-queue'],'other_seven_pids_unchanged':True,'gpu_driver_changes':False,'legacy_generation_worker_changed':False,'external_vision_fallback':False};(B/'vision-router-activation.json').write_text(json.dumps(report,indent=2));print(json.dumps(report))
except BaseException:
 run('systemctl','stop','harmonika-rtx-queue');shutil.copy2(backup/'service.py',Q/'service.py');shutil.copy2(backup/'config.json',CONFIG);DROP.unlink(missing_ok=True)
 run('systemctl','daemon-reload');run('systemctl','start','harmonika-rtx-queue');print(json.dumps({'status':'rolled_back','backup':str(backup),'database_commits_preserved':True}));raise
