#!/usr/bin/env python3
"""Owner-only activation of builder and its gateway route, with scoped rollback."""
import hashlib,json,os,pathlib,shutil,socket,subprocess,time,urllib.request
P=pathlib.Path;ROOT=P('/opt/harmonika-ai');STAGE=ROOT/'builder-all-stage/web_builder';ACTIVE=ROOT/'web_builder';WEB=P('/srv/harmonika-web-chat/current');GW=ROOT/'builder-gateway-stage';SERVICES=('harmonika-ai.service','harmonika-coding-runtime.service','harmonika-telco-engine.service','harmonika-legal-engine.service','harmonika-legal-media.service')
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def run(*args):subprocess.run(args,check=True,stdout=subprocess.DEVNULL)
def pids():return {s:subprocess.check_output(['systemctl','show',s,'-p','MainPID','--value'],text=True).strip() for s in SERVICES}
def probe():
 op=urllib.request.build_opener(urllib.request.ProxyHandler({}))
 for _ in range(50):
  try:
   with op.open('http://127.0.0.1:18191/healthz',timeout=2) as r:health=json.load(r)
   if health.get('service')=='harmonika-web-builder':break
  except Exception:pass
  time.sleep(.2)
 else:raise RuntimeError('builder_health_failed')
 h={'X-Harmonika-Builder-Secret':P('/etc/harmonika-ai/builder-engine.token').read_text().strip(),'X-Harmonika-Principal':'api-token:900000000000400000'}
 with op.open(urllib.request.Request('http://127.0.0.1:18191/capabilities',headers=h),timeout=10) as r:v=json.load(r)
 assert v['implemented_handlers']==161 and len(v['skills'])==161 and v['features']['builder_sandbox'] is True
 return v

def main():
 assert os.geteuid()==0
 test=json.loads((ROOT/'builder-private-smoke-report.json').read_text())['summary'];assert test['passed']==161 and not test['failed']
 baseline=json.loads((GW/'baseline-source.json').read_text())
 for path,digest in baseline.items():assert sha(WEB/path)==digest,'gateway_changed_since_staging'
 s=socket.socket();s.bind(('127.0.0.1',18191));s.close()
 before=pids();backup=P('/var/backups/harmonika-builder')/time.strftime('%Y%m%d-%H%M%S');backup.mkdir(mode=0o700,parents=True);backup.chmod(0o700)
 unit=P('/etc/systemd/system/harmonika-web-builder.service');assert not unit.exists(),'builder_already_installed'
 webfiles=['main.go','public_api_docs.go','harmonika-web'];assert all((WEB/f).exists() for f in webfiles)
 for name in webfiles:shutil.copy2(WEB/name,backup/name)
 assert not (WEB/'public_ai_builder.go').exists(),'gateway_builder_already_present'
 swapped=False;gateway=False
 try:
  if ACTIVE.exists():ACTIVE.rename(backup/'old_builder')
  shutil.copytree(STAGE,ACTIVE,ignore=shutil.ignore_patterns('__pycache__','*.pyc'));swapped=True
  root=P('/var/lib/harmonika-builder');root.mkdir(mode=0o700,exist_ok=True);root.chmod(0o700)
  shutil.copy2(ACTIVE/'deploy/harmonika-web-builder.service',unit);run('systemctl','daemon-reload');run('systemctl','enable','--now','harmonika-web-builder.service');v=probe()
  for name in ['main.go','public_api_docs.go','public_ai_builder.go']:
   temp=WEB/(name+'.builder-next');shutil.copy2(GW/name,temp);temp.replace(WEB/name)
  temp=WEB/'harmonika-web.builder-next';shutil.copy2(GW/'harmonika-web',temp);temp.chmod(0o755);temp.replace(WEB/'harmonika-web');gateway=True
  (root/'ready.json').write_text(json.dumps({'tested_catalog':161,'activated_at':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime())})+'\n')
  run('systemctl','restart','harmonika-web-chat-live.service')
  for _ in range(50):
   if subprocess.check_output(['systemctl','is-active','harmonika-web-chat-live.service'],text=True).strip()=='active':break
   time.sleep(.2)
  assert before==pids(),'unrelated_service_changed'
  report={'status':'activated','catalog':161,'available':sum(x['readiness']['status']=='available' for x in v['skills']),'backup':str(backup),'other_engine_pids_unchanged':True,'only_restarted_existing_service':'harmonika-web-chat-live.service','new_service':'harmonika-web-builder.service','source_files':len([x for x in ACTIVE.rglob('*') if x.is_file()])};(ROOT/'builder-activation.json').write_text(json.dumps(report,indent=2));print(json.dumps(report))
 except Exception:
  if gateway:
   for name in webfiles:shutil.copy2(backup/name,WEB/name)
   (WEB/'public_ai_builder.go').unlink(missing_ok=True);run('systemctl','restart','harmonika-web-chat-live.service')
  if swapped:
   subprocess.run(['systemctl','disable','--now','harmonika-web-builder.service'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL);unit.unlink(missing_ok=True);run('systemctl','daemon-reload');ACTIVE.rename(backup/'failed_builder')
   if (backup/'old_builder').exists():(backup/'old_builder').rename(ACTIVE)
  raise RuntimeError('builder_activation_failed_rolled_back') from None
if __name__=='__main__':main()
