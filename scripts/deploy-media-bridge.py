#!/usr/bin/env python3
"""Owner-run rollout: no renderer/GPU changes, reject any in-flight job."""
import datetime,hashlib,json,os,shutil,sqlite3,subprocess,time,urllib.request,secrets
from pathlib import Path
B=Path('/opt/harmonika-ai');S=B/'media-bridge-stage';W=Path('/srv/harmonika-web-chat/current').resolve();Q=B/'rtx-queue/backend/rtx_queue/service.py';D=Path('/etc/systemd/system/harmonika-rtx-queue.service.d/media-bridge.conf');K=Path('/etc/harmonika-ai/rtx-public-bridge.token')
def run(*a):return subprocess.check_output(a,stderr=subprocess.STDOUT,text=True).strip()
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def atomic(a,b):
 t=b.with_name(b.name+'.media-next');shutil.copy2(a,t);os.replace(t,b)
def health():
 req=urllib.request.Request('http://127.0.0.1:18186/health',headers={'Authorization':'Bearer '+Path('/etc/harmonika-ai/rtx-queue-client.token').read_text().strip()})
 with urllib.request.urlopen(req,timeout=8) as r:return json.load(r)
assert os.geteuid()==0
for p,digest in json.loads((S/'baseline.json').read_text()).items():assert sha(Path(p))==digest,'baseline_changed'
assert not K.exists() and not D.exists(),'already_installed'
with sqlite3.connect('/var/lib/harmonika-rtx-queue/queue.sqlite3') as c:assert c.execute("SELECT count(*) FROM jobs WHERE status NOT IN ('completed','failed','cancelled')").fetchone()[0]==0,'queue_not_idle'
pids=json.loads((S/'pids.json').read_text());assert pids=={s:run('systemctl','show',s,'-p','MainPID','--value') for s in pids},'unrelated_pid_changed'
backup=Path('/var/backups/harmonika-media')/datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ');backup.mkdir(parents=True,mode=0o700)
for n in ('main.go','public_api_docs.go','harmonika-web'):shutil.copy2(W/n,backup/n)
shutil.copy2(Q,backup/'service.py')
with sqlite3.connect('/var/lib/harmonika-rtx-queue/queue.sqlite3') as c,sqlite3.connect(backup/'queue.sqlite3') as out:c.backup(out)
os.chmod(backup/'queue.sqlite3',0o600)
try:
 fd=os.open(K,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
 with os.fdopen(fd,'w') as out:out.write(secrets.token_urlsafe(48)+'\n')
 D.parent.mkdir(exist_ok=True);D.write_text('[Service]\nLoadCredential=bridge.token:/etc/harmonika-ai/rtx-public-bridge.token\n')
 for n in ('main.go','public_api_docs.go','public_ai_media.go','public_ai_media_test.go','harmonika-web'):atomic(S/'gateway'/n,W/n)
 atomic(S/'backend/rtx_queue/service.py',Q)
 run('systemctl','daemon-reload');run('systemctl','restart','harmonika-rtx-queue')
 h=None
 for _ in range(30):
  try:h=health();break
  except OSError:time.sleep(.2)
 assert h and h['ok'] and h['queue_ready'],'queue_health_failed'
 run('systemctl','restart','harmonika-web-chat-live')
 for _ in range(30):
  try:
   with urllib.request.urlopen('http://127.0.0.1:3210/health',timeout=3) as r:assert r.status==200
   break
  except OSError:time.sleep(.2)
 else:raise RuntimeError('gateway_health_failed')
 assert pids=={s:run('systemctl','show',s,'-p','MainPID','--value') for s in pids},'unrelated_service_changed'
 report={'status':'active_pending_public_smoke','backup':str(backup),'worker_capabilities':h['worker'],'only_restarted':['harmonika-rtx-queue','harmonika-web-chat-live'],'unrelated_ten_pids_unchanged':True,'gpu_or_renderer_changed':False,'private_secret_exposed':False}
 (B/'media-bridge-activation.json').write_text(json.dumps(report,indent=2));print(json.dumps(report))
except BaseException:
 for n in ('main.go','public_api_docs.go','harmonika-web'):atomic(backup/n,W/n)
 atomic(backup/'service.py',Q)
 for n in ('public_ai_media.go','public_ai_media_test.go'):(W/n).unlink(missing_ok=True)
 D.unlink(missing_ok=True);K.unlink(missing_ok=True)
 run('systemctl','daemon-reload');run('systemctl','restart','harmonika-rtx-queue');run('systemctl','restart','harmonika-web-chat-live')
 print(json.dumps({'status':'rolled_back','backup':str(backup),'queue_database_preserved':True}));raise
