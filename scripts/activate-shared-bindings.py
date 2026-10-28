"""PC1 source-only guarded activation. Dry-run by default; explicit restart exception required."""
import argparse,hashlib,json,os,shutil,subprocess,time
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--apply',action='store_true');p.add_argument('--restart-existing-master-approved',action='store_true');args=p.parse_args()
root=Path('/opt/harmonika-ai/master-integration-stage');target=Path('/opt/harmonika-ai/master_engine');baseline=json.loads((root/'baseline.json').read_text())
active=hashlib.sha256((target/'service.py').read_bytes()).hexdigest()
assert active in baseline.values(),'Concurrent source changes; review before applying'
for name,digest in json.loads((root/'release-sha256.json').read_text()).items():assert hashlib.sha256((root/'backend/master_engine'/name).read_bytes()).hexdigest()==digest,'Stage integrity mismatch'
print(json.dumps({'source_guard':True,'target_service':'harmonika-master-skills.service','will_restart':args.apply}))
if not args.apply:raise SystemExit(0)
assert args.restart_existing_master_approved,'Explicit user restart exception required'
backup=root/('source-rollback-'+str(int(time.time())));backup.mkdir(mode=0o700)
for name in ['service.py','shared_bindings.py']:
 if (target/name).exists():shutil.copy2(target/name,backup/name)
try:
 for name in ['service.py','shared_bindings.py']:
  tmp=target/(name+'.next');shutil.copy2(root/'backend/master_engine'/name,tmp);os.replace(tmp,target/name)
 subprocess.run(['systemctl','restart','harmonika-master-skills.service'],check=True,timeout=70)
 import urllib.request
 for n in range(15):
  try:
   with urllib.request.urlopen('http://127.0.0.1:18193/healthz',timeout=3) as r:assert r.status==200
   break
  except OSError:
   if n==14:raise
   time.sleep(2)
except Exception:
 for name in ['service.py','shared_bindings.py']:
  if (backup/name).exists():shutil.copy2(backup/name,target/name)
  elif name=='shared_bindings.py':(target/name).unlink(missing_ok=True)
 subprocess.run(['systemctl','restart','harmonika-master-skills.service'],check=True,timeout=70)
 raise
print('{"shared_registry_active":true}')
