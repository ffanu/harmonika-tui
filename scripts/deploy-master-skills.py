#!/usr/bin/env python3
"""Owner-only master index activation. Existing engine PIDs must not change."""
import hashlib,json,os,shutil,socket,subprocess,time,urllib.request
from pathlib import Path
ROOT=Path('/opt/harmonika-ai');CANDIDATE=ROOT/'master-all-stage/master_engine';ACTIVE=ROOT/'master_engine';GW=ROOT/'master-gateway-stage';WEB=Path('/srv/harmonika-web-chat/current');UNIT=Path('/etc/systemd/system/harmonika-master-skills.service')
OTHER=('harmonika-ai.service','harmonika-coding-runtime.service','harmonika-telco-engine.service','harmonika-legal-engine.service','harmonika-legal-media.service','harmonika-web-builder.service')
def run(*args):subprocess.run(args,check=True,stdout=subprocess.DEVNULL)
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def pids():return {x:subprocess.check_output(['systemctl','show',x,'-p','MainPID','--value'],text=True).strip() for x in OTHER}
def probe():
 op=urllib.request.build_opener(urllib.request.ProxyHandler({}))
 for _ in range(50):
  try:
   with op.open('http://127.0.0.1:18193/healthz',timeout=2) as r:ok=json.load(r).get('service')=='harmonika-master-skills'
   if ok:break
  except Exception:pass
  time.sleep(.2)
 else:raise ValueError('master_health_failed')
 h={'X-Harmonika-Master-Secret':Path('/etc/harmonika-ai/master-engine.token').read_text().strip(),'X-Harmonika-Principal':'api-token:900000000000777002'}
 with op.open(urllib.request.Request('http://127.0.0.1:18193/capabilities',headers=h),timeout=5) as r:v=json.load(r)
 assert v['source_unique']==1200 and v['existing_registered']==601 and v['readiness']['available']==29 and v['features']['isolated_html_browser'] is True
 return v

def main():
 assert os.geteuid()==0 and not UNIT.exists()
 test=json.loads((ROOT/'master-private-smoke.json').read_text());assert test['passed']==29 and test['catalog_pagination']==1795
 for f,h in json.loads((GW/'baseline-source.json').read_text()).items():assert sha(WEB/f)==h,'gateway_changed_since_staging'
 s=socket.socket();s.bind(('127.0.0.1',18193));s.close();before=pids();backup=Path('/var/backups/harmonika-master')/time.strftime('%Y%m%d-%H%M%S');backup.mkdir(parents=True,mode=0o700);backup.chmod(0o700)
 for f in ('main.go','public_api_docs.go','harmonika-web'):shutil.copy2(WEB/f,backup/f)
 assert not (WEB/'public_ai_master.go').exists();swapped=False;gateway=False
 try:
  if ACTIVE.exists():ACTIVE.rename(backup/'old_master')
  shutil.copytree(CANDIDATE,ACTIVE,ignore=shutil.ignore_patterns('__pycache__','*.pyc'));swapped=True;shutil.copy2(ACTIVE/'deploy/harmonika-master-skills.service',UNIT);run('systemctl','daemon-reload');run('systemctl','enable','--now','harmonika-master-skills.service');cap=probe()
  for f in ('main.go','public_api_docs.go','public_ai_master.go','harmonika-web'):
   tmp=WEB/(f+'.master-next');shutil.copy2(GW/f,tmp)
   if f=='harmonika-web':tmp.chmod(0o755)
   tmp.replace(WEB/f)
  gateway=True;ready=Path('/var/lib/harmonika-master');ready.mkdir(exist_ok=True,mode=0o700);(ready/'ready.json').write_text(json.dumps({'tested_analyzers':29,'source_primary_unique':1200}));run('systemctl','restart','harmonika-web-chat-live.service');assert before==pids(),'unrelated_service_changed'
  report={'status':'activated','available_analyses_and_browser':29,'source_primary_unique':1200,'catalog_merged':cap['catalog_size'],'readiness':cap['readiness'],'other_six_engine_pids_unchanged':True,'backup':str(backup),'master_source_files':len([p for p in ACTIVE.rglob('*') if p.is_file()])};(ROOT/'master-activation.json').write_text(json.dumps(report,indent=2));print(json.dumps(report))
 except Exception:
  if gateway:
   for f in ('main.go','public_api_docs.go','harmonika-web'):shutil.copy2(backup/f,WEB/f)
   (WEB/'public_ai_master.go').unlink(missing_ok=True);run('systemctl','restart','harmonika-web-chat-live.service')
  if swapped:
   subprocess.run(['systemctl','disable','--now','harmonika-master-skills.service'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL);UNIT.unlink(missing_ok=True);run('systemctl','daemon-reload');ACTIVE.rename(backup/'failed_master')
   if (backup/'old_master').exists():(backup/'old_master').rename(ACTIVE)
  raise RuntimeError('master_activation_failed_rolled_back') from None
if __name__=='__main__':main()
