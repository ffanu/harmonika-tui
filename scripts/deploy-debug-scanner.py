"""Install additive scanner library/plugins. No gateway restart or existing DB access."""
import importlib.util,io,json,shlex,tarfile,uuid
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
s=importlib.util.spec_from_file_location('scanner_bundle',ROOT/'scripts/bundle-debug-scanner.py');b=importlib.util.module_from_spec(s);s.loader.exec_module(b);report=b.bundle(ROOT/'dist/debug-scanner-plugins')
s=importlib.util.spec_from_file_location('scanner_ssh',ROOT/'runtime/tools/ssh_connector.py');ssh=importlib.util.module_from_spec(s);s.loader.exec_module(ssh)
stream=io.BytesIO()
with tarfile.open(fileobj=stream,mode='w:gz') as tar:
 for prefix,folder in [('debug_scanner',ROOT/'backend/debug_scanner'),('plugins',ROOT/'dist/debug-scanner-plugins')]:
  for p in folder.rglob('*'):
   if p.is_file() and '__pycache__' not in p.parts:tar.add(p,arcname=prefix+'/'+p.relative_to(folder).as_posix(),recursive=False)
with ssh.connection(ssh.target(ssh.load_config(),'vps')) as c:
 ftp=c.open_sftp();stage='/opt/harmonika-ai/debug-scanner-stage-'+uuid.uuid4().hex[:12];ftp.mkdir(stage,mode=0o700)
 with ftp.open(stage+'/source.tar.gz','wb') as f:f.write(stream.getvalue())
 ftp.close()
 code=r'''
import pathlib,tarfile,os,json,hashlib,fcntl,ctypes,lzma
stage=pathlib.Path(__import__('sys').argv[1]);root=pathlib.Path('/opt/harmonika-ai');runtime=root/'skills-runtime';library=root/'debug_scanner'
with tarfile.open(stage/'source.tar.gz','r:gz') as tar:tar.extractall(stage,filter='data')
lock=open(root/'.debug-scanner-release.lock','a');fcntl.flock(lock,fcntl.LOCK_EX)
updating=library.exists()
if updating:
 assert not library.is_symlink()
 assert hashlib.sha256((library/'specification.txt').read_bytes()).hexdigest()=='0d274c40408e9a6c2a864807104cb88c76c448e1b67e9918c7383996a45270d3','scanner_spec_conflict'
plugins=list((stage/'plugins').iterdir())
existing_bytes=sum(p.stat().st_size for p in runtime.glob('*/skill.py'));candidate_bytes=sum((p/'skill.py').stat().st_size for p in plugins)
old_bytes=sum((runtime/p.name/'skill.py').stat().st_size for p in plugins if (runtime/p.name).exists());existing_bytes-=old_bytes
assert existing_bytes+candidate_bytes<=64000000,'discovery_source_budget_exceeded'
for p in plugins:
 assert p.name.startswith('debug_scan.')
 if (runtime/p.name).exists():
  assert updating and not (runtime/p.name).is_symlink(),'existing_scanner_plugin_requires_review'
  old=runtime/p.name;assert hashlib.sha256((old/'skill.py').read_bytes()).hexdigest()==json.loads((old/'manifest.json').read_text())['module_sha256']
 pin=json.loads((p/'manifest.json').read_text())['module_sha256'];assert hashlib.sha256((p/'skill.py').read_bytes()).hexdigest()==pin
 for f in p.iterdir():os.chmod(f,0o644)
 os.chmod(p,0o755)
for p in (stage/'debug_scanner').rglob('*'):os.chmod(p,0o755 if p.is_dir() else 0o644)
os.chmod(stage/'debug_scanner',0o755)
lib=ctypes.CDLL(None,use_errno=True)
def swap(src,dest):
 if dest.exists():
  if lib.renameat2(-100,os.fsencode(src),-100,os.fsencode(dest),2)!=0:raise OSError(ctypes.get_errno(),'atomic_scanner_exchange_failed')
 else:os.rename(src,dest)
swap(stage/'debug_scanner',library);installed=[]
try:
 for p in plugins:swap(p,runtime/p.name);installed.append(p.name)
except BaseException:
 for name in reversed(installed):
  if (stage/'plugins'/name).exists():swap(stage/'plugins'/name,runtime/name)
  else:os.rename(runtime/name,stage/'plugins'/name)
 if (stage/'debug_scanner').exists():swap(stage/'debug_scanner',library)
 else:os.rename(library,stage/'debug_scanner')
 raise
config=root/'debug-scanner-targets.json'
try:
 fd=os.open(config,os.O_WRONLY|os.O_CREAT|os.O_EXCL|getattr(os,'O_NOFOLLOW',0),0o644)
 os.fchmod(fd,0o644)
 with os.fdopen(fd,'w') as f:f.write('{"targets":{}}\n')
except FileExistsError:pass
print(json.dumps({'source_bytes_after':existing_bytes+candidate_bytes,'installed_plugins':len(installed),'namespace':'debug_scan','gateway_restarted':False,'existing_business_databases_opened':False,'existing_target_registry_preserved':True,'default_targets_added':0}))
'''
 _,o,e=c.exec_command('python3 -c '+shlex.quote(code)+' '+shlex.quote(stage),timeout=60);out=o.read().decode();error=e.read().decode();status=o.channel.recv_exit_status();assert status==0,error[-800:];print(out)
(ROOT/'docs/DEBUG-SCANNER-PLUGIN-REPORT.json').write_text(json.dumps(report,indent=2)+'\n')
