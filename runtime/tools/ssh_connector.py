"""Registered local SSH/SFTP connector. Never use a shell to launch an SSH client."""
import argparse, base64, hashlib, json, logging, os, re, shlex, socket, sqlite3, stat, sys, time
from pathlib import Path, PurePosixPath
from contextlib import contextmanager

LIMIT = 10 * 1024 * 1024
OUTPUT_LIMIT = 65536
SKILLS = ('ssh.connect', 'ssh.exec', 'ssh.upload', 'ssh.download', 'ssh.tunnel')
FIELDS = {
 'ssh.connect': {'target'},
 'ssh.tunnel': {'target','tunnel'},
 'ssh.exec': {'target', 'argv', 'timeout_seconds'},
 'ssh.upload': {'target', 'project_root', 'local_path', 'remote_path'},
 'ssh.download': {'target', 'project_root', 'local_path', 'remote_path'},
}
class SSHError(Exception):
 def __init__(self, code, uncertain=False): self.code, self.uncertain = code, uncertain

def require(value, code):
 if not value: raise SSHError(code)

def private_file(path, private=True):
 require(os.name == 'posix', 'ssh_platform_not_supported')
 p = Path(path)
 require(p.is_absolute() and not any(x.is_symlink() for x in (p, *p.parents)), 'unsafe_config_file')
 info = p.stat()
 require(stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid() and not info.st_mode & (0o077 if private else 0o022), 'unsafe_config_file')
 require(info.st_size <= 1024 * 1024, 'unsafe_config_file')
 return p

def load_config(path=None):
 require(os.environ.get('SSH_ENABLED','true').lower() not in ('false','0','no'), 'ssh_disabled')
 try:p = private_file(path or os.environ.get('HARMONIKA_SSH_CONFIG', str(Path.home()/'.config/harmonika/ssh-targets.json')))
 except FileNotFoundError:raise SSHError('connector_not_configured') from None
 try: config = json.loads(p.read_text())
 except (ValueError, UnicodeError): raise SSHError('invalid_config') from None
 require(type(config) is dict and config.get('schema_version') == 1 and type(config.get('targets')) is dict and type(config.get('local_roots')) is list, 'invalid_config')
 require(config.get('enabled',True) is True,'ssh_disabled')
 return config

def resolve_alias(config, value):
 if value in config['targets']:return value
 if type(value) is str:
  for alias,t in config['targets'].items():
   endpoint=f"{t['username']}@{t['host']}:{t.get('port',22)}"
   if value in (endpoint,'ssh://'+endpoint):return alias
 raise SSHError('target_not_registered')

def target(config, alias):
 require(type(alias) is str and re.fullmatch(r'[A-Za-z0-9_-]{1,40}', alias), 'invalid_input')
 t = config['targets'].get(alias)
 require(type(t) is dict, 'target_not_registered')
 require(type(t.get('host')) is str and re.fullmatch(r'[A-Za-z0-9.:-]{1,253}', t['host']) and not t['host'].startswith('-'), 'invalid_config')
 require(type(t.get('port',22)) is int and 1 <= t.get('port',22) <= 65535, 'invalid_config')
 require(type(t.get('username')) is str and re.fullmatch(r'[A-Za-z_][A-Za-z0-9_.-]{0,63}', t['username']), 'invalid_config')
 require(t['username'] != 'root' or t.get('allow_root') is True, 'root_not_authorized')
 private_file(t.get('identity_file',''))
 private_file(t.get('known_hosts',''), private=False)
 require(type(t.get('allowed_executables',[])) is list and all(type(x) is str and x.startswith('/') and '\x00' not in x for x in t.get('allowed_executables',[])), 'invalid_config')
 require(type(t.get('remote_root')) is str and t['remote_root'].startswith('/') and '..' not in PurePosixPath(t['remote_root']).parts, 'invalid_config')
 return t

def validate(skill, args):
 require(skill in SKILLS and type(args) is dict and not set(args)-FIELDS[skill] and 'target' in args, 'invalid_input')
 require(type(args['target']) is str, 'invalid_input')
 if skill == 'ssh.exec':
  argv = args.get('argv')
  require(type(argv) is list and 1 <= len(argv) <= 64 and all(type(x) is str and '\x00' not in x and len(x) <= 8192 for x in argv) and sum(len(x) for x in argv)<=16384, 'invalid_input')
  require(type(args.get('timeout_seconds',30)) is int and 1 <= args.get('timeout_seconds',30) <= 120, 'invalid_input')
 if skill in ('ssh.upload','ssh.download'):
  for k in ('project_root','local_path','remote_path'): require(type(args.get(k)) is str and bool(args[k]) and len(args[k])<=1024, 'invalid_input')

def relative(value):
 p = PurePosixPath(value)
 require(not p.is_absolute() and '\x00' not in value and '\\' not in value and not any(x in ('.','..','') or x.startswith('.') for x in value.split('/')), 'path_denied')
 forbidden = {'credentials','secrets','node_modules','venv','library'}
 require(not any(x.casefold() in forbidden for x in p.parts) and p.name.casefold() not in ('id_rsa','id_ed25519','authorized_keys','known_hosts') and not p.suffix.casefold() in ('.pem','.key','.p12','.pfx','.keystore'), 'path_denied')
 return p

def local_path(config, args):
 project = Path(args['project_root'])
 require(project.is_absolute() and '..' not in project.parts and not any(x.startswith('.') for x in project.parts) and project.is_dir() and not any(x.is_symlink() for x in (project,*project.parents)), 'path_denied')
 project = project.resolve()
 roots = [Path(x).resolve() for x in config['local_roots'] if type(x) is str]
 require(any(project != root and project.is_relative_to(root) for root in roots), 'path_denied')
 p = project / str(relative(args['local_path']))
 require(p.parent.is_dir() and not any(x.is_symlink() for x in (p,*p.parents)), 'path_denied')
 require(p.parent.resolve().is_relative_to(project), 'path_denied')
 return p

def remote_path(sftp, t, value):
 root = str(PurePosixPath(t['remote_root']))
 require(sftp.normalize(root) == root, 'path_denied')
 current = '/'
 for part in PurePosixPath(root).parts[1:]:
  current = current.rstrip('/')+'/'+part
  require(stat.S_ISDIR(sftp.lstat(current).st_mode), 'path_denied')
 p = relative(value); current = root
 for part in p.parts[:-1]:
  current += '/' + part
  require(stat.S_ISDIR(sftp.lstat(current).st_mode), 'path_denied')
 dest = current+'/'+p.name
 try: require(not stat.S_ISLNK(sftp.lstat(dest).st_mode), 'path_denied')
 except FileNotFoundError: pass
 return dest

@contextmanager
def connection(t):
 import paramiko
 logger=logging.getLogger('paramiko');logger.addHandler(logging.NullHandler());logger.propagate=False
 client = paramiko.SSHClient()
 try:
  client.load_host_keys(t['known_hosts'])
  client.set_missing_host_key_policy(paramiko.RejectPolicy())
  passphrase = private_file(t['passphrase_file']).read_text().rstrip('\r\n') if t.get('passphrase_file') else None
  key = paramiko.PKey.from_path(t['identity_file'],passphrase=passphrase)
  client.connect(t['host'], port=t.get('port',22), username=t['username'], pkey=key, allow_agent=False, look_for_keys=False, timeout=8, banner_timeout=8, auth_timeout=8, channel_timeout=8)
  yield client
 finally: client.close()

def execute(client, t, args):
 require(args['argv'][0] in t.get('allowed_executables',[]), 'ssh_command_not_allowed')
 timeout = args.get('timeout_seconds',30)
 channel = client.get_transport().open_session(timeout=8)
 started = False; out = bytearray(); err = bytearray(); deadline = time.monotonic()+timeout
 try:
  channel.settimeout(1)
  channel.exec_command(' '.join(shlex.quote(x) for x in args['argv'])); started=True
  while True:
   if time.monotonic() >= deadline: raise SSHError('ssh_timeout', uncertain=True)
   for ready, recv, buf in ((channel.recv_ready,channel.recv,out),(channel.recv_stderr_ready,channel.recv_stderr,err)):
    while ready():
     buf.extend(recv(8192))
     if len(out)+len(err)>OUTPUT_LIMIT: raise SSHError('ssh_output_limit', uncertain=True)
     if time.monotonic()>=deadline:raise SSHError('ssh_timeout', uncertain=True)
   if channel.exit_status_ready() and not channel.recv_ready() and not channel.recv_stderr_ready():
    code=channel.recv_exit_status();break
   if channel.closed and not channel.exit_status_ready(): raise SSHError('remote_state_unknown', uncertain=True)
   time.sleep(.01)
  require(code >= 0, 'remote_state_unknown')
  return {'exit_code':code, 'stdout':out.decode('utf-8','replace'), 'stderr':err.decode('utf-8','replace'), 'executed':True}
 except (OSError,EOFError): raise SSHError('remote_state_unknown' if started else 'ssh_connection_failed',uncertain=started) from None
 finally: channel.close()

def transfer(client, config, t, skill, args):
 upload = skill == 'ssh.upload'; local = local_path(config,args)
 require(t.get('allow_upload' if upload else 'allow_download') is True, 'ssh_transfer_not_allowed')
 sftp=client.open_sftp();sftp.get_channel().settimeout(8);created=False;remote=None;deadline=time.monotonic()+60
 try:
  remote=remote_path(sftp,t,args['remote_path']); digest=hashlib.sha256(); size=0
  if upload:
   fd=os.open(local,os.O_RDONLY|os.O_NOFOLLOW)
   with os.fdopen(fd,'rb') as source:
    require(stat.S_ISREG(os.fstat(source.fileno()).st_mode) and os.fstat(source.fileno()).st_nlink == 1, 'path_denied');require(os.fstat(source.fileno()).st_size<=LIMIT,'file_too_large')
    try: dest=sftp.open(remote,'wx')
    except OSError:
     try:sftp.lstat(remote)
     except OSError:raise SSHError('ssh_transfer_failed') from None
     raise SSHError('destination_exists') from None
    created=True
    with dest:
     while True:
      chunk=source.read(65536)
      if not chunk:break
      size+=len(chunk);require(size<=LIMIT,'file_too_large');require(time.monotonic()<deadline,'ssh_timeout')
      dest.write(chunk);digest.update(chunk)
    sftp.chmod(remote,0o600)
   verification=hashlib.sha256();read_size=0
   with sftp.open(remote,'rb') as source:
    while True:
     chunk=source.read(65536)
     if not chunk:break
     read_size+=len(chunk);require(read_size<=LIMIT and time.monotonic()<deadline,'ssh_integrity_failed');verification.update(chunk)
   require(read_size==size and verification.digest()==digest.digest(),'ssh_integrity_failed')
  else:
   info=sftp.lstat(remote);require(stat.S_ISREG(info.st_mode),'path_denied');require(info.st_size<=LIMIT,'file_too_large')
   try:fd=os.open(local,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
   except FileExistsError:raise SSHError('destination_exists') from None
   created=True
   with os.fdopen(fd,'wb') as dest, sftp.open(remote,'rb') as source:
    while True:
     chunk=source.read(65536)
     if not chunk:break
     size+=len(chunk);require(size<=LIMIT,'file_too_large');require(time.monotonic()<deadline,'ssh_timeout');dest.write(chunk);digest.update(chunk)
   require(size==info.st_size,'ssh_integrity_failed')
  return {'size_bytes':size,'sha256':digest.hexdigest(),'overwritten':False,'transferred':True}
 except BaseException:
  if created:
   try:
    if upload:sftp.remove(remote)
    else:local.unlink()
   except OSError:pass
  raise
 finally:sftp.close()

def audit(skill,status,error,latency):
 base=Path(os.environ.get('HARMONIKA_SSH_STATE_DIR',str(Path.home()/'.local/state/harmonika/ssh')))
 require(base.is_absolute() and not any(p.is_symlink() for p in (base,*base.parents)),'unsafe_state')
 base.mkdir(mode=0o700,parents=True,exist_ok=True);os.chmod(base,0o700);path=base/'audit.sqlite'
 require(not path.is_symlink(),'unsafe_state');fd=os.open(path,os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600);os.close(fd)
 with sqlite3.connect(path,timeout=2) as db:
  db.execute('PRAGMA journal_mode=WAL')
  db.execute('CREATE TABLE IF NOT EXISTS events(timestamp REAL NOT NULL,skill_id TEXT NOT NULL,status TEXT NOT NULL,error_type TEXT NOT NULL,latency_ms INTEGER NOT NULL)')
  db.execute('INSERT INTO events VALUES(?,?,?,?,?)',(time.time(),skill,status,error,latency))
  db.execute('DELETE FROM events WHERE rowid NOT IN (SELECT rowid FROM events ORDER BY rowid DESC LIMIT 5000)')
 os.chmod(path,0o600)

def invoke(skill,args,config_path=None):
 started=time.monotonic();result={'schema_version':1,'skill_id':skill,'provider':'Harmonika Ai'}
 try:
  validate(skill,args);config=load_config(config_path);args={**args,'target':resolve_alias(config,args['target'])};t=target(config,args['target'])
  # Fail closed before any network activity when audit persistence is unavailable.
  if skill == 'ssh.exec':require(args['argv'][0] in t.get('allowed_executables',[]),'ssh_command_not_allowed')
  if skill in ('ssh.upload','ssh.download'):local_path(config,args)
  audit(skill,'started','',0)
  with connection(t) as client:
   if skill=='ssh.connect':data={'target':args['target'],'verified':True,'persistent_session':False,'authentication':'public_key','allowed_executables':t.get('allowed_executables',[]),'upload_enabled':t.get('allow_upload') is True,'download_enabled':t.get('allow_download') is True}
   elif skill=='ssh.exec':data=execute(client,t,args)
   elif skill=='ssh.tunnel':
    profile=t.get('tunnels',{}).get(args.get('tunnel'))
    require(type(profile) is dict and profile.get('host') in ('127.0.0.1','::1','localhost') and type(profile.get('port')) is int and 1<=profile['port']<=65535,'tunnel_not_registered')
    stream=client.get_transport().open_channel('direct-tcpip',(profile['host'],profile['port']),('127.0.0.1',0),timeout=8)
    stream.close();data={'connected':True,'transport':'ssh-direct-tcpip','persistent_tunnel':False,'listener_opened':False}
   else:data=transfer(client,config,t,skill,args)
  ok=skill!='ssh.exec' or data['exit_code']==0
  result.update(ok=ok,status='completed' if ok else 'failed',data=data)
  if not ok:result['error']={'code':'remote_exit_nonzero','retry_safe':False}
 except Exception as exc:
  code=exc.code if isinstance(exc,SSHError) else 'ssh_connection_failed'
  uncertain=bool(getattr(exc,'uncertain',False))
  try:
   import paramiko
   if isinstance(exc,paramiko.BadHostKeyException):code='ssh_host_key_rejected'
   elif isinstance(exc,paramiko.AuthenticationException):code='ssh_auth_failed'
   elif isinstance(exc,paramiko.SSHException) and 'known_hosts' in str(exc):code='ssh_host_key_rejected'
   elif isinstance(exc,(TimeoutError,socket.timeout)):code='ssh_timeout';uncertain=skill in ('ssh.exec','ssh.upload')
   elif isinstance(exc,FileNotFoundError):code='file_not_found'
   elif isinstance(exc,PermissionError):code='path_denied'
  except ImportError:code='ssh_runtime_unavailable'
  result.update(ok=False,status='uncertain' if uncertain else 'error',error={'code':code,'retry_safe':not uncertain and skill=='ssh.connect'})
 try:audit(skill if skill in SKILLS else 'ssh.invalid',result['status'],result.get('error',{}).get('code',''),int((time.monotonic()-started)*1000))
 except Exception:
  result['audit_available']=False
 return result

def main():
 parser=argparse.ArgumentParser();parser.add_argument('action',choices=['invoke','targets']);a=parser.parse_args()
 if a.action=='targets':
  config=load_config();print(json.dumps({'schema_version':1,'ok':True,'targets':[{'target':key,'operations':['ssh.connect']+(['ssh.exec']if t.get('allowed_executables')else[])+(['ssh.upload']if t.get('allow_upload')else[])+(['ssh.download']if t.get('allow_download')else[])}for key,t in config['targets'].items()]}));return
 raw=sys.stdin.buffer.read(32769);require(len(raw)<=32768,'input_too_large');payload=json.loads(raw)
 require(type(payload) is dict and set(payload)=={'skill_id','input'},'invalid_input')
 print(json.dumps(invoke(payload['skill_id'],payload['input']),ensure_ascii=False))
if __name__=='__main__':
 try:main()
 except Exception as exc:
  print(json.dumps({'schema_version':1,'ok':False,'status':'error','error':{'code':exc.code if isinstance(exc,SSHError)else 'invalid_input'}}));sys.exit(2)
