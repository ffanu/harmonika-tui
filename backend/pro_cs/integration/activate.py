"""Source-guarded main API activation. Explicit separate restart exception required."""
import argparse,hashlib,json,os,shutil,subprocess,time,urllib.request
from pathlib import Path
ROOT=Path('/opt/harmonika-ai/pro-cs-engine-stage');TARGET=Path('/opt/harmonika-ai/api');UNIT='harmonika-ai.service'
p=argparse.ArgumentParser();p.add_argument('--apply',action='store_true');p.add_argument('--restart-main-api-approved',action='store_true');a=p.parse_args()
manifest=json.loads((ROOT/'release.json').read_text());baseline=json.loads((ROOT/'baseline.json').read_text())
assert hashlib.sha256((TARGET/'server.js').read_bytes()).hexdigest()==baseline['server.js'],'concurrent_engine_changes'
for name,digest in manifest.items():assert hashlib.sha256((ROOT/'api'/name).read_bytes()).hexdigest()==digest,'stage_changed'
bridge=TARGET/'pro-cs-engine-bridge.cjs'
assert not bridge.exists() or hashlib.sha256(bridge.read_bytes()).hexdigest()==manifest['pro-cs-engine-bridge.cjs'],'unexpected_existing_bridge'
subprocess.run(['/usr/bin/node','--check',str(ROOT/'api/server.js')],check=True)
subprocess.run(['/usr/bin/node','--check',str(ROOT/'api/pro-cs-engine-bridge.cjs')],check=True)
print(json.dumps({'guard_ok':True,'service':UNIT,'apply':a.apply}),flush=True)
if not a.apply:raise SystemExit(0)
assert a.restart_main_api_approved,'explicit_main_API_restart_exception_required'
import psutil
pid=int(subprocess.check_output(['systemctl','show',UNIT,'-p','MainPID','--value'],text=True))
start=psutil.Process(pid).create_time();consecutive=0
for n in range(60):
 process=psutil.Process(pid);assert process.create_time()==start,'engine_pid_changed'
 children=process.children(recursive=True)
 incoming=[c for c in process.net_connections(kind='tcp') if c.status=='ESTABLISHED' and c.laddr.port==17890]
 if not children and not incoming:consecutive+=1
 else:consecutive=0
 if consecutive>=2:break
 time.sleep(2)
else:raise SystemExit('engine_busy_no_restart_performed')
backup=ROOT/('rollback-'+str(int(time.time())));backup.mkdir(mode=0o700)
for name in manifest:
 if (TARGET/name).exists():shutil.copy2(TARGET/name,backup/name)
try:
 for name in manifest:
  temp=TARGET/(name+'.pro-cs-next');shutil.copy2(ROOT/'api'/name,temp);os.replace(temp,TARGET/name)
 subprocess.run(['systemctl','restart',UNIT],check=True,timeout=70)
 healthy=False
 for _ in range(20):
  try:
   req=urllib.request.Request('http://127.0.0.1:17890/health',headers={'Connection':'close'})
   with urllib.request.urlopen(req,timeout=3) as r:healthy=r.status==200 and json.load(r).get('ok') is True
   if healthy:break
  except Exception:pass
  time.sleep(1)
 assert healthy,'engine_health_failed'
except Exception:
 for name in manifest:
  if (backup/name).exists():shutil.copy2(backup/name,TARGET/name)
  else:(TARGET/name).unlink(missing_ok=True)
 subprocess.run(['systemctl','restart',UNIT],check=True,timeout=70)
 raise SystemExit('activation_failed_source_rolled_back')
print(json.dumps({'main_api_restarted':True,'healthy':True,'old_pid':pid,'new_pid':int(subprocess.check_output(['systemctl','show',UNIT,'-p','MainPID','--value'],text=True)),'integration':'advisory_preflight','existing_CS_delivery_guards_unchanged':True}),flush=True)
