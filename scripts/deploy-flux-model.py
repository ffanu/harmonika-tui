#!/usr/bin/env python3
"""Explicit operator activation after technical+visual qualification; no DB migration."""
import datetime,hashlib,json,os,shutil,subprocess,time,urllib.request
from pathlib import Path
ROOT=Path('/opt/harmonika-ai');STAGE=ROOT/'flux-stage';CONFIG=Path('/etc/harmonika-ai/rtx-queue/config.json')
FILES={'router':ROOT/'lib/rtx-image-router.js','bridge':ROOT/'crush-bridge/worker.py','config':CONFIG}
def run(*a):return subprocess.check_output(a,text=True,stderr=subprocess.STDOUT).strip()
def digest(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def get(url):
 with urllib.request.urlopen(url,timeout=5) as r:return json.load(r)
def main():
 assert os.geteuid()==0
 report=json.loads((STAGE/'qualification-report.json').read_text());visual=json.loads((STAGE/'visual-review.json').read_text())
 assert report['status']=='technical_passed_visual_review_required' and len(report['cases'])==5
 assert visual['approved'] is True and visual['model']=='black-forest-labs/FLUX.2-klein-4B'
 health=get('http://127.0.0.1:18211/health');assert health['ok'] and health['approved'] and health['model']==visual['model']
 baseline=json.loads((STAGE/'activation-baseline.json').read_text())
 assert all(digest(p)==baseline[n] for n,p in FILES.items()),'source_changed_since_review'
 sql="import sqlite3; c=sqlite3.connect('/var/lib/harmonika-rtx-queue/queue.sqlite3'); print(c.execute(\"SELECT count(*) FROM jobs WHERE status NOT IN ('completed','failed','cancelled')\").fetchone()[0]); c.close()"
 assert run('runuser','-u','harmonika-rtx-queue','--','/usr/bin/python3','-c',sql)=='0','queue_not_idle'
 assert not run('ss','-Htn','state','established','sport','=',':17890'),'engine_has_active_request'
 backup=Path('/var/backups/harmonika-media')/('flux-'+datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ'));backup.mkdir(mode=0o700,parents=True)
 for n,p in FILES.items():shutil.copy2(p,backup/n)
 services=['harmonika-web-chat-live','harmonika-shared-core','harmonika-unified','harmonika-master-skills','harmonika-rtx-tunnel','harmonika-rtx-vision-tunnel']
 pids={s:run('systemctl','show',s,'-p','MainPID','--value') for s in services}
 stopped=False;engine_restarted=False
 try:
  run('systemctl','stop','harmonika-rtx-queue');stopped=True
  for n in ('router','bridge'):
   tmp=FILES[n].with_name(FILES[n].name+'.flux-next');shutil.copy2(STAGE/n,tmp);os.replace(tmp,FILES[n])
  cfg=json.loads(CONFIG.read_text());assert cfg['endpoint']=='http://127.0.0.1:18182';cfg['endpoint']='http://127.0.0.1:18211'
  tmp=CONFIG.with_suffix('.flux-next');tmp.write_text(json.dumps(cfg));tmp.chmod(0o600);os.replace(tmp,CONFIG)
  assert not run('ss','-Htn','state','established','sport','=',':17890'),'engine_became_busy'
  run('systemctl','restart','harmonika-ai');engine_restarted=True
  run('systemctl','start','harmonika-rtx-queue');stopped=False
  for _ in range(30):
   try:
    get('http://127.0.0.1:17890/health')
    token=Path('/etc/harmonika-ai/rtx-queue-client.token').read_text().strip()
    req=urllib.request.Request('http://127.0.0.1:18186/health',headers={'Authorization':'Bearer '+token})
    with urllib.request.urlopen(req,timeout=3) as r:v=json.load(r)
    if v['worker']['generate'] and v['queue_ready']:break
   except Exception:pass
   time.sleep(.5)
  else:raise RuntimeError('activation_health_failed')
  assert pids=={s:run('systemctl','show',s,'-p','MainPID','--value') for s in services},'unrelated_service_changed'
  state={'status':'active_pending_end_to_end','model':health['model'],'endpoint':'loopback18211','backup':str(backup),'only_restarted':['harmonika-ai','harmonika-rtx-queue'],'other_service_pids_unchanged':True,'database_unchanged':True,'legacy_windows_worker_untouched':True,'old_hashes':baseline,'new_hashes':{n:digest(p) for n,p in FILES.items()}}
  (ROOT/'flux-model-activation.json').write_text(json.dumps(state,indent=2));print(json.dumps(state))
 except BaseException:
  run('systemctl','stop','harmonika-rtx-queue')
  for n,p in FILES.items():shutil.copy2(backup/n,p)
  if engine_restarted:run('systemctl','restart','harmonika-ai')
  run('systemctl','start','harmonika-rtx-queue')
  print(json.dumps({'status':'rolled_back','backup':str(backup)}));raise
if __name__=='__main__':main()
