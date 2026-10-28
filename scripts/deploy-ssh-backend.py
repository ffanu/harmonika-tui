#!/usr/bin/env python3
"""Manual private SSH backend installation; no main gateway restart or secret export."""
import hashlib,importlib.util,io,json,os,shlex,tarfile,time
from pathlib import Path
SOURCE=Path(__file__).resolve().parents[1]
s=importlib.util.spec_from_file_location('owner_ssh',SOURCE/'runtime/tools/ssh_connector.py');ssh=importlib.util.module_from_spec(s);s.loader.exec_module(ssh)
config=ssh.load_config();server=ssh.target(config,'vps');destination=ssh.target(config,'chr')
files={f'remote_access/{p.name}':p for p in (SOURCE/'backend/remote_access').glob('*.py')}
files['remote_access/ssh_connector.py']=SOURCE/'runtime/tools/ssh_connector.py'
files['remote_access/client.cjs']=SOURCE/'backend/remote_access/client.cjs'
files['README.md']=SOURCE/'backend/remote_access/README.md'
for p in (SOURCE/'backend/remote_access/monitoring').iterdir():files['monitoring/'+p.name]=p
files['requirements.lock']=SOURCE/'backend/remote_access/requirements.lock'
files['harmonika-remote-access.service']=SOURCE/'backend/remote_access/deploy/harmonika-remote-access.service'
archive=io.BytesIO()
with tarfile.open(fileobj=archive,mode='w:gz') as tar:
 for name,p in files.items():tar.add(p,arcname=name,recursive=False)
manifest={name:hashlib.sha256(p.read_bytes()).hexdigest() for name,p in files.items()}
bootstrap=r'''
import hashlib,json,os,pathlib,pwd,secrets,shutil,subprocess,tarfile
base=pathlib.Path('/opt/harmonika-ai/remote-access');cfg=pathlib.Path('/etc/harmonika-remote');state=pathlib.Path('/var/lib/harmonika-remote-access')
assert not (base/'current').exists(),'existing_installation_requires_review'
try:user=pwd.getpwnam('harmonika-remote')
except KeyError:
 subprocess.run(['useradd','--system','--home-dir',str(state),'--shell','/usr/sbin/nologin','harmonika-remote'],check=True);user=pwd.getpwnam('harmonika-remote')
for directory in [cfg,state,state/'projects',state/'projects'/'smoke',state/'stage',state/'stage'/'projects',state/'stage'/'projects'/'smoke']:
 directory.mkdir(parents=True,exist_ok=True,mode=0o700);os.chmod(directory,0o700);os.chown(directory,user.pw_uid,user.pw_gid)
release=base/'releases/2026.10.26-ssh1';release.mkdir(parents=True,exist_ok=False)
with tarfile.open(base/'upload.tar.gz','r:gz') as tar:tar.extractall(release,filter='data')
(base/'current').symlink_to(release)
for stage in (False,True):
 prefix='stage-' if stage else ''
 key=cfg/(prefix+'identity')
 subprocess.run(['ssh-keygen','-q','-t','ed25519','-N','','-C','harmonika-backend-'+('stage' if stage else 'prod'),'-f',str(key)],check=True)
 for suffix in ('','.pub'):os.chown(str(key)+suffix,user.pw_uid,user.pw_gid);os.chmod(str(key)+suffix,0o600)
 for name in ('api.token','approval.token'):
  p=cfg/(prefix+name);p.write_text(secrets.token_urlsafe(48));p.chmod(0o600);os.chown(p,user.pw_uid,user.pw_gid)
subprocess.run(['/usr/bin/python3','-m','venv',str(base/'venv')],check=True)
subprocess.run([str(base/'venv/bin/python'),'-m','pip','install','--disable-pip-version-check','--quiet','-r',str(release/'requirements.lock')],check=True,timeout=240)
print(json.dumps({'prepared':True,'openssh_client':bool(shutil.which('ssh')),'release':'2026.10.26-ssh1'}))
'''
with ssh.connection(server) as client:
 ftp=client.open_sftp()
 try:ftp.mkdir('/opt/harmonika-ai/remote-access',mode=0o755)
 except OSError:raise RuntimeError('existing_remote_access_directory_review_required') from None
 with ftp.open('/opt/harmonika-ai/remote-access/upload.tar.gz','wb') as f:f.write(archive.getvalue())
 def run(code,timeout=240):
  _,out,err=client.exec_command('/usr/bin/python3 -u -c '+shlex.quote(code),timeout=timeout)
  output=out.read().decode();error=err.read().decode();status=out.channel.recv_exit_status()
  if status:raise RuntimeError('remote_step_failed:'+error[-1500:])
  return output
 print(run(bootstrap))
 public={}
 for name in ('identity.pub','stage-identity.pub'):
  with ftp.open('/etc/harmonika-remote/'+name,'r') as f:public[name]=f.read().decode().strip()
 with ssh.connection(destination) as target:
  sftp=target.open_sftp();path='/root/.ssh/authorized_keys'
  with sftp.open(path,'rb') as f:old=f.read()
  additions=[v for v in public.values() if v.split()[:2] not in [line.decode().split()[:2] for line in old.splitlines()]]
  if additions:
   with sftp.open(path,'ab') as f:f.write(('\n' if old and not old.endswith(b'\n') else '')+'\n'.join(additions)+'\n')
  remote='/var/tmp/harmonika-backend-ssh'
  try:sftp.mkdir(remote,mode=0o700)
  except OSError:
   import stat
   info=sftp.lstat(remote);assert stat.S_ISDIR(info.st_mode) and info.st_uid==0 and not info.st_mode&0o077
  sftp.close()
 # Only public host pins cross machines. No existing owner private key is copied.
 import paramiko
 known=paramiko.HostKeys(destination['known_hosts']);host='['+destination['host']+']:'+str(destination['port']);pins=known.lookup(host);assert pins,'verified_host_pin_required'
 pin=''.join(host+' '+kind+' '+key.get_base64()+'\n' for kind,key in pins.items())
 settings={'host':destination['host'],'port':destination['port'],'username':destination['username'],'allow_root':True,'known_hosts':'/etc/harmonika-remote/known_hosts','remote_root':'/var/tmp/harmonika-backend-ssh','allow_upload':True,'allow_download':True,'allowed_executables':['/usr/bin/uname','/usr/bin/id','/usr/bin/df','/usr/bin/uptime'],'tunnels':{'vnc':{'host':'127.0.0.1','port':5901}}}
 bundle={'known_hosts':pin}
 for stage in (False,True):
  prefix='stage-' if stage else '';root='/var/lib/harmonika-remote-access'+('/stage' if stage else '')
  bundle[prefix+'targets.json']=json.dumps({'schema_version':1,'enabled':True,'local_roots':[root+'/projects'],'targets':{'chr':{**settings,'identity_file':'/etc/harmonika-remote/'+prefix+'identity'}}},indent=2)
 for name,content in bundle.items():
  with ftp.open('/etc/harmonika-remote/'+name,'w') as f:f.write(content)
  ftp.chmod('/etc/harmonika-remote/'+name,0o600)
 ftp.close()
 print(run("import os,pwd,pathlib,json;u=pwd.getpwnam('harmonika-remote');[(os.chown(p,u.pw_uid,u.pw_gid),os.chmod(p,0o600)) for p in pathlib.Path('/etc/harmonika-remote').iterdir()];print(json.dumps({'configured':True,'owner_private_key_exported':False}))"))
 # Start only our newly created staging service, isolated from production state.
 stageunit=(SOURCE/'backend/remote_access/deploy/harmonika-remote-access.service').read_text().replace('/etc/harmonika-remote/targets.json','/etc/harmonika-remote/stage-targets.json').replace('/etc/harmonika-remote/api.token','/etc/harmonika-remote/stage-api.token').replace('/etc/harmonika-remote/approval.token','/etc/harmonika-remote/stage-approval.token').replace('HARMONIKA_REMOTE_ROOT=/var/lib/harmonika-remote-access','HARMONIKA_REMOTE_ROOT=/var/lib/harmonika-remote-access/stage').replace('HARMONIKA_SSH_STATE_DIR=/var/lib/harmonika-remote-access/worker-audit','HARMONIKA_SSH_STATE_DIR=/var/lib/harmonika-remote-access/stage/worker-audit').replace('HARMONIKA_REMOTE_PORT=18231','HARMONIKA_REMOTE_PORT=18232')
 ftp=client.open_sftp()
 with ftp.open('/etc/systemd/system/harmonika-remote-access-staging.service','w') as f:f.write(stageunit)
 ftp.close()
 print(run("import subprocess,json;subprocess.run(['systemctl','daemon-reload'],check=True);subprocess.run(['systemctl','start','harmonika-remote-access-staging.service'],check=True);print(json.dumps({'staging_started':True,'port':18232}))"))
 (SOURCE/'docs/SSH-BACKEND-SOURCE-MANIFEST.json').write_text(json.dumps(manifest,indent=2)+'\n')
