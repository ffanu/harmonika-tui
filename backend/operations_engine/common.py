"""Strict JSON, private files and registered scope helpers. No content logging."""
import json,os,re,stat
from pathlib import Path
ALIAS=re.compile(r'^[a-z][a-z0-9_-]{0,63}$')
PRINCIPAL=re.compile(r'^api-token:[1-9][0-9]{0,18}$')
class OpsError(ValueError):
 def __init__(self,code,status=400):super().__init__(code);self.code=code;self.status=status

def require(value,code,status=400):
 if not value:raise OpsError(code,status)

def fields(value,allowed,required=()):
 require(type(value) is dict and not set(value)-set(allowed) and all(k in value for k in required),'invalid_input')

def private_path(path,directory=False,create=False):
 p=Path(path).absolute()
 require(not any(x.is_symlink() for x in [p,*p.parents]),'unsafe_private_path')
 if create:
  if directory:p.mkdir(parents=True,exist_ok=True,mode=0o700)
  elif not p.exists():
   fd=os.open(p,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600);os.close(fd)
 st=p.stat();require(stat.S_ISDIR(st.st_mode) if directory else stat.S_ISREG(st.st_mode) and st.st_nlink==1,'unsafe_private_path')
 if os.name!='nt':require(st.st_uid==os.getuid() and not st.st_mode&0o077,'unsafe_private_permissions')
 return p

def load_private(path):
 p=private_path(path);require(p.stat().st_size<=262144,'configuration_too_large',503)
 value=decode(p.read_bytes());require(type(value) is dict,'invalid_configuration',503);return value

def decode(raw):
 require(len(raw)<=1048576,'input_too_large',413)
 def pairs(items):
  out={}
  for k,v in items:
   require(k not in out,'duplicate_json_key');out[k]=v
  return out
 try:value=json.loads(raw,object_pairs_hook=pairs,parse_constant=lambda _:(_ for _ in ()).throw(OpsError('invalid_json')))
 except (ValueError,UnicodeError,RecursionError):raise OpsError('invalid_json') from None
 pending=[(value,0)];count=0
 while pending:
  v,d=pending.pop();count+=1;require(d<=24 and count<=20000,'json_complexity')
  if type(v) is dict:pending.extend((x,d+1) for x in v.values())
  elif type(v) is list:pending.extend((x,d+1) for x in v)
 return value

def authorization(config,principal,project=None,admin=False):
 require(type(principal) is str and PRINCIPAL.fullmatch(principal),'access_denied',403)
 grant=config.get('principals',{}).get(principal)
 if grant is None and config.get('allow_gateway_principals') is True:grant={'role':'user','projects':[]}
 require(type(grant) is dict,'access_denied',403)
 require(not admin or grant.get('role')=='admin','admin_required',403)
 if project is not None:
  require(type(project) is str and ALIAS.fullmatch(project),'invalid_project')
  require(project in grant.get('projects',[]),'project_denied',403)
  require(project in config.get('projects',{}),'project_not_configured',503)
 return grant
