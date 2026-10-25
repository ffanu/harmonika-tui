#!/usr/bin/env python3
"""Owner-only activation after private smoke. Restore previous gateway/module on failure."""
import hashlib,json,os,shutil,subprocess,time,urllib.request
from pathlib import Path
ROOT=Path('/opt/harmonika-ai');STAGE=ROOT/'mobile-module-stage';GW=ROOT/'mobile-gateway-stage';WEB=Path('/srv/harmonika-web-chat/current');ACTIVE=ROOT/'mobile_engine'
OTHER=('harmonika-ai.service','harmonika-coding-runtime.service','harmonika-telco-engine.service','harmonika-legal-engine.service','harmonika-legal-media.service','harmonika-web-builder.service')
FILES=('main.go','public_api_docs.go','public_ai_mobile.go','harmonika-web')
def run(*args):subprocess.run(args,check=True,stdout=subprocess.DEVNULL)
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def pids():return {x:subprocess.check_output(['systemctl','show',x,'-p','MainPID','--value'],text=True).strip() for x in OTHER}
def probe(expected):
 secret=Path('/etc/harmonika-ai/master-engine.token').read_text().strip();headers={'X-Harmonika-Master-Secret':secret,'X-Harmonika-Principal':'api-token:90000000000777999'};op=urllib.request.build_opener(urllib.request.ProxyHandler({}))
 for _ in range(70):
  try:
   with op.open(urllib.request.Request('http://127.0.0.1:18193/mobile/capabilities',headers=headers),timeout=2) as r:caps=json.load(r)
   assert caps['catalog_size']==214 and caps['readiness']==expected
   return caps
  except Exception:time.sleep(.2)
 raise ValueError('mobile_probe_failed')
def main():
 assert os.geteuid()==0 and not ACTIVE.exists()
 test=json.loads((ROOT/'mobile-private-smoke.json').read_text());assert test['passed']==test['readiness']['available'] and test['catalog_size']==214 and test['curl_capabilities_passed']
 assert {'scaffold.expo','scaffold.flutter','scaffold.kotlin_android','scaffold.swift_ios','scaffold.ionic'}<={r['id'] for r in test['results']},'wait_for_opencode_generators'
 for f,h in json.loads((GW/'baseline-source.json').read_text()).items():assert sha(WEB/f)==h,'gateway_changed'
 expected=json.loads((STAGE/'active-master-baseline.json').read_text())['service_sha256'];assert sha(ROOT/'master_engine/service.py')==expected
 backup=Path('/var/backups/harmonika-mobile')/time.strftime('%Y%m%d-%H%M%S');backup.mkdir(parents=True,mode=0o700);backup.chmod(0o700)
 for f in FILES:
  if (WEB/f).exists():shutil.copy2(WEB/f,backup/f)
 shutil.copy2(ROOT/'master_engine/service.py',backup/'master-service.py');before=pids();gateway=False;module=False
 try:
  shutil.copytree(STAGE/'backend/mobile_engine',ACTIVE,ignore=shutil.ignore_patterns('__pycache__','*.pyc'));module=True
  tmp=ROOT/'master_engine/service.mobile-next';shutil.copy2(STAGE/'backend/master_engine/service.py',tmp);tmp.replace(ROOT/'master_engine/service.py')
  run('systemctl','restart','harmonika-master-skills.service');caps=probe(test['readiness'])
  ready=Path('/var/lib/harmonika-master/mobile-ready.json');ready.write_text(json.dumps({'catalog_size':214,'tested':test['passed']}));ready.chmod(0o600)
  gateway=True
  for f in FILES:
   tmp=WEB/(f+'.mobile-next');shutil.copy2(GW/f,tmp)
   if f=='harmonika-web':tmp.chmod(0o755)
   tmp.replace(WEB/f)
  run('systemctl','restart','harmonika-web-chat-live.service');assert before==pids(),'unrelated_engine_pid_changed'
  report={'status':'activated','capabilities':caps,'other_six_engine_pids_unchanged':True,'backup':str(backup),'service':'harmonika-master-skills.service','gateway':'harmonika-web-chat-live.service'}
  (ROOT/'mobile-activation.json').write_text(json.dumps(report,indent=2));print(json.dumps(report))
 except Exception:
  if gateway:
   for f in FILES:
    if (backup/f).exists():shutil.copy2(backup/f,WEB/f)
    elif f=='public_ai_mobile.go':(WEB/f).unlink(missing_ok=True)
   run('systemctl','restart','harmonika-web-chat-live.service')
  (Path('/var/lib/harmonika-master')/'mobile-ready.json').unlink(missing_ok=True)
  shutil.copy2(backup/'master-service.py',ROOT/'master_engine/service.py')
  if module:ACTIVE.rename(backup/'failed_mobile_module')
  run('systemctl','restart','harmonika-master-skills.service')
  raise RuntimeError('mobile_activation_failed_rolled_back') from None
if __name__=='__main__':main()
