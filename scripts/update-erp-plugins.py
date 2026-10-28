"""Install reviewed ERP plugins atomically; serialize updates without service restart."""
import importlib.util,io,json,shlex,tarfile,uuid
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
s=importlib.util.spec_from_file_location('bundle',ROOT/'scripts/bundle-erp-plugins.py');b=importlib.util.module_from_spec(s);s.loader.exec_module(b);plugins=ROOT/'dist/erp-plugins';report=b.bundle(plugins)
s=importlib.util.spec_from_file_location('ssh',ROOT/'runtime/tools/ssh_connector.py');ssh=importlib.util.module_from_spec(s);s.loader.exec_module(ssh)
stream=io.BytesIO()
with tarfile.open(fileobj=stream,mode='w:gz') as tar:
 for path in plugins.rglob('*'):
  if path.is_file():tar.add(path,arcname='plugins/'+path.relative_to(plugins).as_posix(),recursive=False)
 for path in (ROOT/'backend/erp_engine').rglob('*'):
  if path.is_file() and '__pycache__' not in path.parts:tar.add(path,arcname='engine/'+path.relative_to(ROOT/'backend/erp_engine').as_posix(),recursive=False)
with ssh.connection(ssh.target(ssh.load_config(),'vps')) as client:
 ftp=client.open_sftp();stage='/opt/harmonika-ai/erp-engine-update-'+uuid.uuid4().hex[:12];ftp.mkdir(stage,mode=0o700)
 with ftp.open(stage+'/update.tar.gz','wb') as f:f.write(stream.getvalue())
 ftp.close()
 code=r'''
import ctypes,hashlib,json,os,pathlib,tarfile,fcntl,sys
stage=pathlib.Path(sys.argv[1]);root=pathlib.Path('/opt/harmonika-ai/skills-runtime')
with tarfile.open(stage/'update.tar.gz','r:gz') as tar:tar.extractall(stage,filter='data')
lib=ctypes.CDLL(None,use_errno=True);count=new=0
# Validate all candidates before the first switch.
for p in (stage/'plugins').iterdir():
 assert p.name.startswith('erp_all.') and p.is_dir()
 for directory in [p]+([root/p.name] if (root/p.name).exists() else []):
  assert not directory.is_symlink()
  pin=json.loads((directory/'manifest.json').read_text())['module_sha256'];assert hashlib.sha256((directory/'skill.py').read_bytes()).hexdigest()==pin
 for file in p.iterdir():os.chmod(file,0o644)
 os.chmod(p,0o755)
lock=open('/opt/harmonika-ai/.erp-release.lock','a');fcntl.flock(lock,fcntl.LOCK_EX)
def exchange(src,dest):
 if lib.renameat2(-100,os.fsencode(src),-100,os.fsencode(dest),2)!=0:raise OSError(ctypes.get_errno(),'atomic_plugin_exchange_failed')
for p in (stage/'plugins').iterdir():
 dest=root/p.name
 if dest.exists():exchange(p,dest)
 else:os.rename(p,dest);new+=1
 count+=1
exchange(stage/'engine',pathlib.Path('/opt/harmonika-ai/erp_engine'))
print(json.dumps({'updated_plugins':count,'new_plugins':new,'atomic_exchange':True,'gateway_restarted':False}))
'''
 _,o,e=client.exec_command('/usr/bin/python3 -c '+shlex.quote(code)+' '+shlex.quote(stage),timeout=60);out=o.read().decode();error=e.read().decode();status=o.channel.recv_exit_status();print(out);assert status==0,error[-1200:]
