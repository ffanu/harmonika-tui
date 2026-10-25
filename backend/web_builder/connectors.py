"""Operator-registered SSH. No target/address/key supplied by a public client."""
import asyncio,base64,json,os,re,shlex,hashlib
from pathlib import Path
from .common import require,BuilderError,safe_path,principal
class Connectors:
 def __init__(self,path='/etc/harmonika-ai/builder-connectors.json'):
  self.targets={};self.deployments={};self.notion={};self.ready=False
  p=Path(path)
  if not p.exists():return
  require(not p.is_symlink() and p.stat().st_mode&0o077==0,'unsafe_connector_config',503)
  v=json.loads(p.read_text());self.targets=v.get('targets',{});self.deployments=v.get('deployments',{});self.notion=v.get('notion',{});self.ready=bool(self.targets)
  require(type(self.targets) is dict and type(self.deployments) is dict and type(self.notion) is dict,'invalid_connector_config',503)
  for t in list(self.targets.values())+list(self.deployments.values())+list(self.notion.values()):
   require(type(t) is dict and type(t.get('principals')) is list,'invalid_connector_config',503)
   for a in t['principals']:principal(a)
 def target(self,alias,actor):
  t=self.targets.get(alias);require(type(t) is dict and actor in t.get('principals',[]),'ssh_target_not_authorized',403)
  require(type(t.get('port',22)) is int and 1<=t.get('port',22)<=65535,'invalid_connector_config',503)
  require(t.get('user')!='root' and re.fullmatch(r'[a-z_][a-z0-9_-]{0,31}',t.get('user','')),'ssh_unprivileged_account_required',503)
  require(type(t.get('host')) is str and re.fullmatch(r'[A-Za-z0-9.:-]{1,253}',t['host']) is not None and not t['host'].startswith('-'),'invalid_connector_config',503)
  for field in ('identity_file','known_hosts'):
   p=Path(t[field]);require(p.is_file() and not p.is_symlink() and p==p.resolve() and str(p).startswith('/etc/harmonika-ai/builder-targets/'),'invalid_connector_config',503)
  return t
 async def health(self,t,action):
  argv=['ssh','-o','BatchMode=yes','-o','ConnectTimeout=8','-o','ServerAliveInterval=3','-o','ServerAliveCountMax=2','-o','StrictHostKeyChecking=yes','-o','IdentitiesOnly=yes','-o','UserKnownHostsFile='+t['known_hosts'],'-i',t['identity_file'],'-p',str(t.get('port',22)),t['user']+'@'+t['host'],'timeout 8 uname -s' if action=='health' else 'timeout 8 df -Pk .']
  proc=await asyncio.create_subprocess_exec(*argv,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.DEVNULL)
  out=await limited_output(proc,None,8192,15)
  require(proc.returncode==0 and len(out)<=8192,'ssh_connection_failed',502)
  return out.decode('utf-8','replace')

async def limited_output(proc,raw,limit,timeout):
 async def write():
  if proc.stdin:
   if raw:proc.stdin.write(raw);await proc.stdin.drain()
   proc.stdin.close()
 async def read():
  chunks=[];total=0
  while True:
   chunk=await proc.stdout.read(min(65536,limit-total+1))
   if not chunk:return b''.join(chunks)
   total+=len(chunk);require(total<=limit,'ssh_output_limit',502);chunks.append(chunk)
 try:
  _,out,_=await asyncio.wait_for(asyncio.gather(write(),read(),proc.wait()),timeout)
  return out
 except BaseException:
  if proc.returncode is None:proc.kill() # Only the client just created by this connector call.
  await proc.wait()
  raise

REMOTE_FILE_IO = r"""
import os,sys,stat,base64,json,hashlib,secrets
mode,root,path=sys.argv[1:]
parts=path.split('/')
if not (root.startswith('/') and '..' not in parts and all(parts)):raise ValueError('invalid_path')
flags=os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW
fd=os.open('/',flags)
for base_part in root.strip('/').split('/'):
 if base_part in ('','.','..'):raise ValueError('canonical_root_required')
 next_fd=os.open(base_part,flags,dir_fd=fd);os.close(fd);fd=next_fd
try:
 for part in parts[:-1]:
  child=os.open(part,flags,dir_fd=fd);os.close(fd);fd=child
 name=parts[-1]
 if mode=='upload':
  raw=sys.stdin.buffer.read(524289)
  if len(raw)>524288:raise ValueError('file_too_large')
  temp='.harmonika-upload-'+secrets.token_hex(16)
  try:
   file=os.open(temp,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=fd)
   with os.fdopen(file,'wb') as out:out.write(raw);out.flush();os.fsync(out.fileno())
   os.link(temp,name,src_dir_fd=fd,dst_dir_fd=fd,follow_symlinks=False)
  finally:
   try:os.unlink(temp,dir_fd=fd)
   except FileNotFoundError:pass
  result={'size_bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest()}
 else:
  file=os.open(name,os.O_RDONLY|os.O_NOFOLLOW,dir_fd=fd)
  with os.fdopen(file,'rb') as src:
   if not stat.S_ISREG(os.fstat(src.fileno()).st_mode):raise ValueError('regular_file_required')
   raw=src.read(524289)
   if len(raw)>524288:raise ValueError('file_too_large')
  result={'bytes_base64':base64.b64encode(raw).decode(),'sha256':hashlib.sha256(raw).hexdigest()}
 print(json.dumps(result))
finally:os.close(fd)
"""
async def transfer(t,mode,path,raw=None):
 root=t.get('root');require(type(root) is str and root.startswith('/') and '\n' not in root and '..' not in root.split('/'),'invalid_connector_root',503)
 command='python3 -c '+shlex.quote(REMOTE_FILE_IO)+' '+shlex.quote(mode)+' '+shlex.quote(root)+' '+shlex.quote(path)
 argv=['ssh','-o','BatchMode=yes','-o','ConnectTimeout=8','-o','ServerAliveInterval=3','-o','ServerAliveCountMax=2','-o','StrictHostKeyChecking=yes','-o','IdentitiesOnly=yes','-o','UserKnownHostsFile='+t['known_hosts'],'-i',t['identity_file'],'-p',str(t.get('port',22)),t['user']+'@'+t['host'],command]
 proc=await asyncio.create_subprocess_exec(*argv,stdin=asyncio.subprocess.PIPE,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.DEVNULL)
 try:out=await limited_output(proc,raw,750000,20)
 except asyncio.TimeoutError:raise BuilderError('ssh_transfer_interrupted',504) from None
 require(proc.returncode==0 and len(out)<=750000,'ssh_transfer_failed',502)
 try:v=json.loads(out)
 except ValueError:raise BuilderError('ssh_invalid_response',502) from None
 require(type(v) is dict,'ssh_invalid_response',502)
 if raw is not None:require(v.get('sha256')==hashlib.sha256(raw).hexdigest(),'ssh_integrity_failed',502)
 return v
async def dispatch(connectors,skill,args,files,j,actor):
 # Root injects the validated principal, never a public JSON field.
 if skill.startswith('ssh.setup.'):
  packages={'node':'nodejs npm','python':'python3 python3-venv','docker':'docker.io docker-compose-plugin','nginx':'nginx','certbot':'certbot python3-certbot-nginx','firewall':'ufw','fail2ban':'fail2ban'}
  package=packages[skill.rsplit('.',1)[-1]]
  script='# Operator review required. No commands were executed.\n# Keep the existing SSH port allowed before enabling any firewall.\nsudo apt-get update\nsudo apt-get install --no-install-recommends '+package+'\n'
  return {'status':'completed','execution_kind':'operator_recipe','data':{'executed':False,'os_family':'debian_ubuntu','requires_operator_review':True},'writes':[{'path':'deploy/ssh/'+skill.rsplit('.',1)[-1]+'.sh','content':script}],'limitations':['Package installation recipe only; no firewall reset, upgrades, or host service changes.']}
 require(connectors is not None and connectors.ready,'ssh_connector_not_configured',503)
 t=connectors.target(args.get('target'),actor)
 if skill in ('ssh.upload','ssh.download'):
  path=safe_path(args.get('path'));mode=skill.split('.')[1]
  if mode=='upload':
   require(path in files,'file_not_found',404);v=await transfer(t,mode,path,base64.b64decode(files[path]));return {'status':'completed','execution_kind':'registered_ssh_upload','data':{**v,'target':args['target'],'path':path,'overwritten':False,'remote_mutation_started':True}}
  require(path not in files,'file_exists',409);v=await transfer(t,mode,path);encoded=v.get('bytes_base64');require(type(encoded) is str,'ssh_invalid_response',502);raw=base64.b64decode(encoded,validate=True);require(hashlib.sha256(raw).hexdigest()==v.get('sha256'),'ssh_integrity_failed',502);return {'status':'completed','execution_kind':'registered_ssh_download','data':{'path':path,'sha256':v['sha256']},'_snapshot':{**files,path:encoded}}
 if skill in ('ssh.connect','ssh.exec'):
  text=await connectors.health(t,args.get('action','health'));return {'status':'completed','execution_kind':'registered_ssh_read','data':{'target':args['target'],'text':text},'limitations':['Registered unprivileged read-only command; no host setup or deploy executed.']}
 # Configuration/artifact can be requested without executing arbitrary remote shell.
 if skill=='ssh.tunnel':
  lp=args['local_port'];rp=args['remote_port'];cmd='ssh -N -o ExitOnForwardFailure=yes -o StrictHostKeyChecking=yes -L '+shlex.quote('127.0.0.1:'+str(lp)+':127.0.0.1:'+str(rp))+' '+shlex.quote(t['user']+'@'+t['host'])
 else:cmd={'ssh.upload':'Use SFTP with the registered account and an operator-reviewed destination.','ssh.download':'Use SFTP with the registered account and an operator-reviewed source.'}.get(skill,'Review the generated server setup recipe before executing on a registered target.')
 return {'status':'completed','execution_kind':'operator_recipe','data':{'target':args['target'],'command':cmd,'executed':False,'requires_operator_execution':True},'limitations':['No remote mutation is enabled by this recipe.']}
