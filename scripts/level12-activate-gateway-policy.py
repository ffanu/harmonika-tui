"""CAS-guarded policy error fix; preserves dirty Go tree, data, assets and core PIDs."""
import fcntl,json,shlex,subprocess
from pathlib import Path
R=Path(Path('/tmp/harmonika-level12-root.path').read_text().strip());lock=(R/'native.lock').open('a');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB);stage=json.loads((R/'gateway-policy-stage-result.json').read_text())['stage'];assert json.loads((R/'gateway-policy-regression.json').read_text())['passed']
code=r'''import fcntl,hashlib,json,os,shutil,subprocess,sys,time,urllib.request
from pathlib import Path
stage=Path(json.load(sys.stdin)['stage']);proof=json.loads((stage/'proof.json').read_text());baseline=json.loads((stage/'baseline.json').read_text());base=Path('/srv/harmonika-web-chat/current').resolve();state=stage/'activation.json'
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def pid(name):return subprocess.check_output(['systemctl','show',name,'-p','MainPID','--value']).decode().strip()
def health():
 out={}
 for label,url in [('local','http://127.0.0.1:3210/health'),('public','https://chat.harmonika.id/health')]:
  try:
   r=urllib.request.urlopen(url,timeout=8);out[label]=r.status
  except Exception:out[label]=0
 return out
with open('/run/lock/harmonika-web-policy-activation.lock','a') as lock:
 fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
 if state.exists():raise RuntimeError('activation_already_attempted_reconcile_only')
 if str(base)!=baseline['source'] or not proof['tests_passed'] or not proof['build_passed'] or not json.loads((stage/'regression-proof.json').read_text())['passed']:raise RuntimeError('verification_changed')
 if any(sha(base/n)!=v for n,v in baseline['hashes'].items()):raise RuntimeError('live_source_drift')
 if sha(stage/'harmonika-web')!=proof['binary_sha256']:raise RuntimeError('binary_drift')
 others={u:pid(u) for u in ['harmonika-ai','harmonika-shared-core']};oldpid=pid('harmonika-web-chat-live');backup=stage/'backup';backup.mkdir(mode=0o700)
 names=['public_ai_internal.go','public-tool-policy-errors.go','public-tool-policy-errors_test.go','public-tool-policy-integration_test.go'];existing=[]
 for name in names+['harmonika-web']:
  if (base/name).exists():shutil.copy2(base/name,backup/name);existing.append(name)
 state.write_text(json.dumps({'status':'intent','old_pid':oldpid,'source':str(base),'others':others}));os.chmod(state,0o600)
 try:
  for name in names:
   temp=base/(name+'.level12-next');shutil.copy2(stage/'source'/name,temp);os.replace(temp,base/name)
  temp=base/'harmonika-web.level12-next';shutil.copy2(stage/'harmonika-web',temp);os.chmod(temp,0o755);os.replace(temp,base/'harmonika-web')
  subprocess.run(['systemctl','restart','harmonika-web-chat-live'],check=True,capture_output=True,timeout=35)
  for _ in range(30):
   h=health()
   if all(v==200 for v in h.values()):break
   time.sleep(.3)
  else:raise RuntimeError('gateway_health_failed')
  if any(pid(u)!=v for u,v in others.items()):raise RuntimeError('unrelated_service_pid_changed')
  result={'status':'active','source':str(base),'changed':names+['harmonika-web'],'health':h,'gateway_pid_changed':pid('harmonika-web-chat-live')!=oldpid,'core_and_shared_pids_unchanged':True,'binary_sha256':sha(base/'harmonika-web'),'rollback_backup':str(backup),'data_and_assets_changed':False,'at':time.time()};state.write_text(json.dumps(result,indent=2));print(json.dumps(result))
 except Exception:
  for name in names+['harmonika-web']:
   if name in existing:
    temp=base/(name+'.level12-rollback');shutil.copy2(backup/name,temp);os.replace(temp,base/name)
   else:(base/name).unlink(missing_ok=True)
  subprocess.run(['systemctl','restart','harmonika-web-chat-live'],check=True,capture_output=True,timeout=35);result={'status':'rolled_back','health':health(),'at':time.time()};state.write_text(json.dumps(result));print(json.dumps(result));sys.exit(1)
'''
p=subprocess.run(['ssh','-o','BatchMode=yes','-o','StrictHostKeyChecking=yes','vps','python3 -c '+shlex.quote(code)],input=json.dumps({'stage':stage}),capture_output=True,text=True,timeout=150);(R/'gateway-policy-activation.json').write_text(p.stdout);(R/'gateway-policy-activation.stderr').write_text(p.stderr);print(p.stdout if p.stdout else json.dumps({'transport_exit':p.returncode}));raise SystemExit(p.returncode)
