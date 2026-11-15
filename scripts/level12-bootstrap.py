"""Bootstrap only one verified disposable Linux exam host and new owned DNS."""
import base64,hashlib,importlib.util,json,os,secrets
from pathlib import Path
REPO=Path(__file__).resolve().parents[1];R=Path(Path('/tmp/harmonika-level12-root.path').read_text().strip());D=R/'operator';M=json.loads((R/'lab.json').read_text())
def mod(name,path):
 s=importlib.util.spec_from_file_location(name,str(path));m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m
T=mod('transport',REPO/'scripts/level12-operator.py')
# Scope/identity/resource checks precede first mutation.
result=T.python("import hashlib,json,subprocess;from pathlib import Path;print(json.dumps({'machine':hashlib.sha256(Path('/etc/machine-id').read_bytes()).hexdigest(),'ports':subprocess.check_output(['ss','-Hltn']).decode(),'free':int(next(x.split()[1] for x in Path('/proc/meminfo').read_text().splitlines() if x.startswith('MemAvailable:')))}))")
assert result['machine']=='a9baadf374fdfbacaa7ac7d7562109b32111821b583170a94d423de4ef4e65eb'
assert not any(':'+str(p)+' ' in result['ports'] for p in [80,443,17881,20219]);assert result['free']>300000
assert json.loads((D/'caddy-provenance.json').read_text())['binary_sha256']==hashlib.sha256((D/'caddy').read_bytes()).hexdigest()
if not (D/'auth.json').exists():
 auth={u:secrets.token_urlsafe(28) for u in ['admin','staff-a','staff-b']};(D/'auth.json').write_text(json.dumps(auth));os.chmod(D/'auth.json',0o600)
auth=json.loads((D/'auth.json').read_text());users={}
for u,password in auth.items():
 salt=secrets.token_hex(16);users[u]={'role':'admin' if u=='admin' else 'staff','salt':salt,'hash':hashlib.pbkdf2_hmac('sha256',password.encode(),bytes.fromhex(salt),180000).hex()}
# Upload bounded fixed files; no candidate code is executed as root.
import paramiko
t=T.connection()
try:
 s=paramiko.SFTPClient.from_transport(t);base=M['remote_root']
 for p in ['/opt/harmonika-exams',base,base+'/bin',base+'/releases',base+'/receipts']:
  try:s.mkdir(p,mode=0o755)
  except IOError:
   assert s.stat(p).st_mode&0o170000==0o040000
 files={'control.py':(REPO/'scripts/level12-lab/remote_control.py').read_bytes(),'meta.json':json.dumps(M).encode(),'auth-hashes.json':json.dumps({'users':users}).encode(),'bin/caddy':(D/'caddy').read_bytes()}
 for n,b in files.items():
  with s.file(base+'/'+n,'wb') as f:f.write(b)
  s.chmod(base+'/'+n,0o755 if n=='bin/caddy' else 0o640 if n=='auth-hashes.json' else 0o600)
 s.close()
finally:t.close()
code="""import json,pwd,subprocess,os;from pathlib import Path
m=json.load(__import__('sys').stdin);u=m['service_user'];base=Path(m['remote_root']);data=Path(m['data_dir'])
try:pwd.getpwnam(u)
except KeyError:subprocess.run(['useradd','--system','--no-create-home','--shell','/usr/sbin/nologin',u],check=True)
p=pwd.getpwnam(u);data.mkdir(mode=0o700,exist_ok=True);os.chown(data,p.pw_uid,p.pw_gid);os.chown(base/'auth-hashes.json',0,p.pw_gid)
print(json.dumps({'owned_user':u,'data_dir_created':True,'global_daemons_installed':False}))
"""
print(json.dumps(T.python(code,M)))
CF=mod('exam_dns','/Users/id/.config/harmonika/access/cloudflare_exam.py');dns=CF.ExamDNS();value=dns.create_a(M['domain'],target='38.47.176.62',proxied=False);(D/'dns-receipt.json').write_text(json.dumps(value,indent=2));M['dns_created']=True;(R/'lab.json').write_text(json.dumps(M,indent=2));print(json.dumps({'dns_created':True,'domain':M['domain'],'target':M['host'],'proxied':False}))
(D/'controller.py').write_bytes((REPO/'scripts/level12-lab/controller.py').read_bytes())
