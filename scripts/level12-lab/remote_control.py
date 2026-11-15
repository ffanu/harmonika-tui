"""Root-owned narrow control plane for one disposable exam, never generic shell."""
import base64,fcntl,hashlib,json,os,re,shutil,sqlite3,subprocess,sys,time
from pathlib import Path
os.umask(0o077)
BASE=Path(__file__).resolve().parent;M=json.loads((BASE/'meta.json').read_text());DATA=Path(M['data_dir']);UNITS=[M['app_unit'],M['edge_unit']]
def run(args,check=True):
 p=subprocess.run(args,capture_output=True,text=True,timeout=35)
 if check and p.returncode:raise RuntimeError('owned_command_failed')
 return p.stdout.strip()
def digest(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def atomic(path,data):
 t=path.with_suffix('.tmp');t.write_text(json.dumps(data,sort_keys=True));os.chmod(t,0o600)
 with t.open('rb') as f:os.fsync(f.fileno())
 os.replace(t,path);d=os.open(path.parent,os.O_RDONLY)
 try:os.fsync(d)
 finally:os.close(d)
def read_state(path):
 for _ in range(5):
  try:return json.loads(path.read_text())
  except (ValueError,FileNotFoundError):time.sleep(.005)
 raise RuntimeError('state_incomplete')
def status():
 def unit(n):
  raw=run(['systemctl','show',n,'-p','ActiveState','-p','MainPID','-p','MemoryCurrent','-p','MemoryMax','-p','CPUUsageNSec','-p','NRestarts'],False);return dict(x.split('=',1) for x in raw.splitlines() if '=' in x)
 a=unit(M['app_unit']);e=unit(M['edge_unit']);ready=json.loads((DATA/'ready.json').read_text()) if (DATA/'ready.json').exists() else None
 valid=bool(isinstance(ready,dict) and type(ready.get('pid')) is int and type(ready.get('port')) is int and int(a.get('MainPID','0'))==ready['pid'] and 1024<=ready['port']<=65535 and ready['port']!=3000)
 return {'app':a,'edge':e,'ready':ready if valid else None,'pid_bound_ready':valid,'current':json.loads((BASE/'current.json').read_text()) if (BASE/'current.json').exists() else None,'schema':schema()}
def schema():
 p=DATA/'crm.sqlite'
 if not p.exists():return None
 c=sqlite3.connect(p,timeout=.2)
 try:return {'version':c.execute('PRAGMA user_version').fetchone()[0],'journal':c.execute('PRAGMA journal_mode').fetchone()[0],'integrity':c.execute('PRAGMA quick_check').fetchone()[0]}
 finally:c.close()
def caddy_config(port):
 return '{\n admin 127.0.0.1:'+str(M['edge_admin_port'])+'\n auto_https disable_redirects\n}\nhttp://'+M['domain']+' {\n redir https://'+M['domain']+'{uri} permanent\n}\n'+M['domain']+' {\n header Strict-Transport-Security "max-age=86400"\n reverse_proxy 127.0.0.1:'+str(port)+'\n}\n'
def deploy(release,operation):
 if not re.fullmatch(r'v[12](?:-r[1-3])?',release or '') or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}',operation or ''):raise RuntimeError('invalid_identity')
 src=BASE/'releases'/release;manifest=json.loads((src/'manifest.json').read_text());receipts=BASE/'receipts';receipts.mkdir(exist_ok=True);receipt=receipts/(operation+'.json')
 if receipt.exists():
  old=json.loads(receipt.read_text())
  if old['release']!=release or old['source_sha256']!=manifest['source_sha256']:raise RuntimeError('operation_conflict')
  if old.get('state')!='completed':raise RuntimeError('operation_incomplete')
  return {**old,'historic_receipt':True,'current_state':status()}
 if {str(p.relative_to(src)) for p in src.rglob('*') if p.is_file()}!=set(manifest['hashes'])|{'manifest.json'}:raise RuntimeError('release_file_set_invalid')
 for name,sha in manifest['hashes'].items():
  if digest(src/name)!=sha:raise RuntimeError('release_integrity_failed')
 atomic(receipt,{'release':release,'operation':operation,'state':'intent','source_sha256':manifest['source_sha256']})
 run(['systemctl','stop',M['app_unit']],False);(DATA/'ready.json').unlink(missing_ok=True)
 env='PORT='+str(M['preferred_port'])+'\nAPP_RELEASE='+release+'\nDATA_DIR='+str(DATA)+'\nAUTH_CONFIG='+str(BASE/'auth-hashes.json')+'\nPUBLIC_ORIGIN=https://'+M['domain']+'\n'
 write_env(env)
 unit='[Unit]\nDescription=Harmonika isolated Level12 CRM\nAfter=network.target\n[Service]\nType=simple\nUser='+M['service_user']+'\nGroup='+M['service_user']+'\nWorkingDirectory='+str(src)+'\nEnvironmentFile='+str(BASE/'app.env')+'\nEnvironment=PYTHONDONTWRITEBYTECODE=1\nExecStart=/usr/bin/python3 '+str(src/'app.py')+'\nRestart=on-failure\nRestartSec=2\nMemoryMax=256M\nCPUQuota=60%\nTasksMax=32\nUMask=0077\nNoNewPrivileges=yes\nProtectSystem=strict\nProtectHome=yes\nPrivateTmp=yes\nReadWritePaths='+str(DATA)+'\nRestrictAddressFamilies=AF_INET AF_UNIX\n[Install]\nWantedBy=multi-user.target\n'
 Path('/etc/systemd/system',M['app_unit']).write_text(unit);run(['systemctl','daemon-reload']);run(['systemctl','reset-failed',M['app_unit']],False);run(['systemctl','start',M['app_unit']])
 end=time.monotonic()+20;st=status()
 while time.monotonic()<end and not(st['pid_bound_ready'] and st['ready']['release']==release):time.sleep(.3);st=status()
 if not(st['pid_bound_ready'] and st['ready']['release']==release):raise RuntimeError('application_not_ready')
 # Validate actual listening socket belongs to current PID before configuring edge.
 sockets=run(['ss','-Hltnp']);needle='127.0.0.1:'+str(st['ready']['port'])
 if not any(needle in line and 'pid='+str(st['ready']['pid'])+',' in line for line in sockets.splitlines()):raise RuntimeError('listener_identity_failed')
 (BASE/'Caddyfile').write_text(caddy_config(st['ready']['port']));os.chmod(BASE/'Caddyfile',0o644)
 edge='[Unit]\nDescription=Harmonika isolated Level12 TLS edge\nAfter=network.target\n[Service]\nUser='+M['service_user']+'\nGroup='+M['service_user']+'\nEnvironment=HOME='+str(DATA)+'\nExecStart='+str(BASE/'bin/caddy')+' run --config '+str(BASE/'Caddyfile')+' --adapter caddyfile\nRestart=on-failure\nRestartSec=3\nMemoryMax=128M\nCPUQuota=30%\nTasksMax=32\nUMask=0077\nAmbientCapabilities=CAP_NET_BIND_SERVICE\nCapabilityBoundingSet=CAP_NET_BIND_SERVICE\nNoNewPrivileges=yes\nProtectSystem=strict\nProtectHome=yes\nPrivateTmp=yes\nReadWritePaths='+str(DATA)+'\n[Install]\nWantedBy=multi-user.target\n'
 Path('/etc/systemd/system',M['edge_unit']).write_text(edge);run(['systemctl','daemon-reload']);run(['systemctl','restart',M['edge_unit']]);run(['systemctl','enable',M['app_unit'],M['edge_unit']]);atomic(BASE/'current.json',manifest)
 value={'release':release,'operation':operation,'state':'completed','source_sha256':manifest['source_sha256'],'domain':M['domain'],'service_unit':M['app_unit'],'at':time.time(),'port':st['ready']['port'],'pid':st['ready']['pid']};atomic(receipt,value);return {**value,'current_state':status()}
def write_env(text):
 p=BASE/'app.env';tmp=BASE/'app.env.tmp';tmp.write_text(text);os.chmod(tmp,0o600)
 with tmp.open('rb') as f:os.fsync(f.fileno())
 os.replace(tmp,p)
def restart_owned():
 before=status();expected=before['current']['release'] if before.get('current') else None
 run(['systemctl','reset-failed',M['app_unit']],False);run(['systemctl','restart',M['app_unit']]);end=time.monotonic()+20
 while time.monotonic()<end:
  current=status()
  if current['pid_bound_ready'] and (expected is None or current['ready']['release']==expected):break
  time.sleep(.25)
 else:raise RuntimeError('restart_readiness_failed')
 sockets=run(['ss','-Hltnp']);needle='127.0.0.1:'+str(current['ready']['port'])
 if not any(needle in line and 'pid='+str(current['ready']['pid'])+',' in line for line in sockets.splitlines()):raise RuntimeError('listener_identity_failed')
 (BASE/'Caddyfile').write_text(caddy_config(current['ready']['port']));os.chmod(BASE/'Caddyfile',0o644);run(['systemctl','restart',M['edge_unit']]);return {'before_pid':before['app']['MainPID'],'after':current}
def fault(kind,action):
 if kind=='restart':return restart_owned()
 if kind not in ['lock','port','bad-config','writer']:raise RuntimeError('fault_unknown')
 flag=BASE/(kind+'-release');state=BASE/(kind+'-state.json')
 if action=='release':flag.touch();return {'released':kind}
 if action=='state':return read_state(state) if state.exists() else {'active':False}
 if action!='start':raise RuntimeError('fault_action_invalid')
 if state.exists() and time.time()-read_state(state).get('started',0)<40:raise RuntimeError('fault_already_started')
 flag.unlink(missing_ok=True)
 if kind=='bad-config':
  current=json.loads((BASE/'current.json').read_text());env=BASE/'app.env';original=env.read_text();write_env(original.replace('AUTH_CONFIG=','INVALID_AUTH_CONFIG=')+'AUTH_CONFIG=/nonexistent/fixture.json\n');run(['systemctl','restart',M['app_unit']],False);time.sleep(2);s=status();run(['systemctl','stop',M['app_unit']],False);write_env(original);return {'fault':'bad-config','failed_as_expected':not s['pid_bound_ready'],'state':s,'recovery':'fault restart'}
 if kind=='port':
  st=status();lines=run(['ss','-Hltnp']).splitlines();owners=[l for l in lines if '127.0.0.1:'+str(M['preferred_port'])+' ' in l]
  if owners and not all('pid='+st['app'].get('MainPID','0')+',' in l for l in owners):raise RuntimeError('port_busy_foreign')
  run(['systemctl','stop',M['app_unit']],False)
 if kind=='writer':
  code="import json,sqlite3,time;from pathlib import Path;base=Path(__file__).parent;m=json.loads((base/'meta.json').read_text());c=sqlite3.connect(Path(m['data_dir'])/'crm.sqlite',timeout=.2);c.execute('CREATE TABLE IF NOT EXISTS _exam_ticks(id INTEGER PRIMARY KEY, value INTEGER NOT NULL)');c.execute('INSERT OR IGNORE INTO _exam_ticks VALUES(1,0)');c.commit();started=time.time();state=base/'writer-state.json';flag=base/'writer-release';\nwhile time.time()-started<30 and not flag.exists():\n try:\n  c.execute('UPDATE _exam_ticks SET value=value+1 WHERE id=1');c.commit();n=c.execute('SELECT value FROM _exam_ticks WHERE id=1').fetchone()[0];state.write_text(json.dumps({'active':True,'kind':'writer','started':started,'ticks':n}));time.sleep(.02)\n except sqlite3.OperationalError:c.rollback();time.sleep(.02)\nc.close();state.write_text(json.dumps({'active':False,'kind':'writer','started':started,'ticks':n}))"
  helper=BASE/'writer-holder.py';helper.write_text(code);subprocess.Popen(['/usr/bin/python3',str(helper)],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True)
  for _ in range(40):
   if state.exists() and read_state(state).get('active'):return read_state(state)
   time.sleep(.05)
  raise RuntimeError('writer_holder_not_ready')
 code="import json,socket,sqlite3,time;from pathlib import Path;m=json.loads(Path(__file__).with_name('meta.json').read_text());base=Path(__file__).parent;kind=KIND;flag=base/(kind+'-release');state=base/(kind+'-state.json');started=time.time();resource=sqlite3.connect(Path(m['data_dir'])/'crm.sqlite',timeout=.2) if kind=='lock' else socket.socket();resource.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1) if kind=='port' else None;resource.execute('BEGIN IMMEDIATE') if kind=='lock' else resource.bind(('127.0.0.1',m['preferred_port']));resource.listen(1) if kind=='port' else None;state.write_text(json.dumps({'active':True,'kind':kind,'started':started}));\nwhile time.time()-started<30 and not flag.exists():time.sleep(.1)\nresource.close();state.write_text(json.dumps({'active':False,'kind':kind,'started':started}))"
 helper=BASE/(kind+'-holder.py');helper.write_text(code.replace('KIND',repr(kind)));subprocess.Popen(['/usr/bin/python3',str(helper)],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True)
 for _ in range(40):
  if state.exists() and read_state(state).get('active'):
   result=read_state(state)
   if kind=='port':result['restart']=restart_owned()
   return result
  time.sleep(.05)
 raise RuntimeError('fault_holder_not_ready')
def invariants(path):
 c=sqlite3.connect(path,timeout=.2)
 try:
  c.execute('BEGIN');result={'version':c.execute('PRAGMA user_version').fetchone()[0],'counts':{},'rows_sha256':{}}
  for name in ['customers','tasks','articles']:
   rows=c.execute('SELECT * FROM '+name+' ORDER BY id').fetchall();result['counts'][name]=len(rows);result['rows_sha256'][name]=hashlib.sha256(json.dumps(rows,sort_keys=True).encode()).hexdigest()
  return result
 finally:c.rollback();c.close()
def backup(label):
 if not re.fullmatch(r'[a-z][a-z0-9-]{0,40}',label or ''):raise RuntimeError('label_invalid')
 parent=BASE/'backups';parent.mkdir(mode=0o700,exist_ok=True);final=parent/label
 if final.exists():raise RuntimeError('backup_exists')
 d=parent/('.tmp-'+label);d.mkdir(mode=0o700,exist_ok=False);out=d/'crm.sqlite';deadline=time.monotonic()+15
 try:
  c=sqlite3.connect(DATA/'crm.sqlite',timeout=.2);target=sqlite3.connect(out)
  def progress(*args):
   if time.monotonic()>deadline:raise RuntimeError('backup_deadline')
  try:c.backup(target,pages=20,progress=progress,sleep=.05)
  finally:target.close();c.close()
  os.chmod(out,0o600)
  with out.open('rb') as f:os.fsync(f.fileno())
  c=sqlite3.connect(out)
  try:integrity=c.execute('PRAGMA integrity_check').fetchone()[0]
  finally:c.close()
  if integrity!='ok':raise RuntimeError('backup_integrity_failed')
  manifest={'label':label,'sha256':digest(out),'closed_snapshot':True,'integrity':integrity,'data_invariants':invariants(out),'created':time.time(),'writer_state':read_state(BASE/'writer-state.json') if (BASE/'writer-state.json').exists() else None}
  atomic(d/'manifest.json',manifest);os.rename(d,final);fd=os.open(parent,os.O_RDONLY)
  try:os.fsync(fd)
  finally:os.close(fd)
  return manifest
 except Exception:
  shutil.rmtree(d);raise
def restore(label):
 if not re.fullmatch(r'[a-z][a-z0-9-]{0,40}',label or ''):raise RuntimeError('label_invalid')
 src=BASE/'backups'/label;manifest=json.loads((src/'manifest.json').read_text())
 if digest(src/'crm.sqlite')!=manifest['sha256']:raise RuntimeError('backup_hash_mismatch')
 before=invariants(DATA/'crm.sqlite');d=BASE/'clones'/label;d.mkdir(parents=True,mode=0o700,exist_ok=False);shutil.copyfile(src/'crm.sqlite',d/'crm.sqlite');os.chmod(d/'crm.sqlite',0o600)
 c=sqlite3.connect(d/'crm.sqlite')
 try:integrity=c.execute('PRAGMA integrity_check').fetchone()[0]
 finally:c.close()
 clone=invariants(d/'crm.sqlite');after=invariants(DATA/'crm.sqlite')
 return {'clone':label,'sha256':digest(d/'crm.sqlite'),'matches_backup':digest(d/'crm.sqlite')==manifest['sha256'],'integrity':integrity,'counts':clone['counts'],'snapshot_data_match':clone==manifest['data_invariants'],'live_database_unchanged':before==after,'live_before':before,'live_after':after}
def main():
 if hashlib.sha256(Path('/etc/machine-id').read_bytes()).hexdigest()!='a9baadf374fdfbacaa7ac7d7562109b32111821b583170a94d423de4ef4e65eb':raise RuntimeError('machine_identity_changed')
 q=json.load(sys.stdin);globals()['_request']=q;lock=(BASE/'control.lock').open('a');fcntl.flock(lock,fcntl.LOCK_EX);op=q.get('op')
 if op in ['status','resources']:value=status()
 elif op=='deploy':value=deploy(q.get('release'),q.get('operation'))
 elif op=='fault':value=fault(q.get('kind'),q.get('action'))
 elif op=='backup':value=backup(q.get('label'))
 elif op=='restore':value=restore(q.get('label'))
 elif op=='history':value=[json.loads(p.read_text()) for p in sorted((BASE/'receipts').glob('*.json'))]
 elif op=='audit':
  c=sqlite3.connect(DATA/'crm.sqlite',timeout=.2);value={k:c.execute('SELECT count(*) FROM '+k).fetchone()[0] for k in ['customers','tasks','articles']};c.close();value={'counts':value,'database':schema()}
 elif op=='logs':
  raw=run(['journalctl','-u',M['app_unit'],'--no-pager','-o','cat','-n','150'],False);value={'lines':len(raw.splitlines()),'traceback':'Traceback' in raw,'sensitive_marker':any(s in raw for s in ['hl12_session=','csrf','password','EXAM-SENTINEL'])}
 else:raise RuntimeError('operation_denied')
 if isinstance(value,dict):
  current=json.loads((BASE/'current.json').read_text()) if (BASE/'current.json').exists() else None
  value['_source_binding']={k:current[k] for k in ['release','source_sha256']} if current else None
 print(json.dumps(value))
if __name__=='__main__':
 try:main()
 except Exception as e:
  q=globals().get('_request',{});identity=q.get('operation','')
  if q.get('op')=='deploy' and re.fullmatch(r'[A-Za-z0-9_-]{1,64}',identity):
   receipt=BASE/'receipts'/(identity+'.json')
   if receipt.exists():
    previous=json.loads(receipt.read_text())
    if previous.get('state')=='intent':atomic(receipt,{**previous,'state':'failed','error_type':type(e).__name__,'at':time.time()})
  print(json.dumps({'error':str(e) if isinstance(e,RuntimeError) and re.fullmatch('[a-z_]+',str(e)) else type(e).__name__}));sys.exit(1)
