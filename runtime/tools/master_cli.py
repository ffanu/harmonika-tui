"""Harmonika consolidated skill discovery; secret never appears in argv/output."""
import argparse,json,os,stat,sys,time,urllib.request,urllib.error
from pathlib import Path
from urllib.parse import urlencode,quote
BASE='https://chat.harmonika.id/v1/skills'
class NoRedirect(urllib.request.HTTPRedirectHandler):
 def redirect_request(self,*args,**kwargs):raise ValueError('redirect_refused')
def wait_for_discovery(fetch,timeout=30.0,clock=time.monotonic,sleep=time.sleep):
 deadline=clock()+timeout
 while True:
  value=fetch()
  if type(value) is not dict or value.get('error'):raise ValueError('discovery_status_unavailable')
  busy=value.get('scanning') is True or value.get('result')=='E_SCAN_BUSY' or value.get('status') in ('scanning','recovering')
  if not busy:return value
  remaining=deadline-clock()
  if remaining<=0:raise ValueError('discovery_scan_busy')
  delay=value.get('retry_after_ms',100)
  if type(delay) not in (int,float) or not 10<=delay<=1000:delay=100
  sleep(min(delay/1000,remaining))

def request(method,path,body=None,idempotency_key=None,*,base=BASE,timeout=65):
 if base not in (BASE,"https://chat.harmonika.id/v1/mobile-builder","https://chat.harmonika.id/v1/core"):raise ValueError("unsupported_endpoint")
 fd=os.open(Path.home()/'.config/harmonika/harmonika-api-key',os.O_RDONLY|getattr(os,'O_NOFOLLOW',0)|getattr(os,'O_NONBLOCK',0))
 with os.fdopen(fd) as f:
  s=os.fstat(f.fileno())
  if not stat.S_ISREG(s.st_mode) or (os.name!='nt' and (s.st_uid!=os.getuid() or s.st_mode&0o077)):raise ValueError('unsafe_key_file')
  key=f.read(256).strip()
 if not key.startswith('hai_') or len(key)>200:raise ValueError('invalid_key_file')
 if method=='POST' and path=='/invoke' and base==BASE and body is not None and not body.get('context'):
  # Retry only read-only status checks. Never retry a POST/tool execution.
  wait_for_discovery(lambda:request('GET','/discovery',base=base,timeout=2))
 payload=json.dumps(body,ensure_ascii=False).encode() if body is not None else None
 if payload and len(payload)>1048576:raise ValueError('input_too_large')
 headers={'Authorization':'Bearer '+key,'Content-Type':'application/json'}
 if method=='POST':
  from uuid import uuid4
  headers['Idempotency-Key']=idempotency_key or str(uuid4())
 req=urllib.request.Request(base+path,method=method,data=payload,headers=headers)
 try:
  with urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect()).open(req,timeout=timeout) as r:raw=r.read(1048577)
 except urllib.error.HTTPError as e:raw=e.read(65536)
 if len(raw)>1048576:raise ValueError('response_too_large')
 return json.loads(raw)
def main(argv=None):
 p=argparse.ArgumentParser(description=__doc__);sub=p.add_subparsers(dest='command',required=True);sub.add_parser('capabilities')
 c=sub.add_parser('list');c.add_argument('--status');c.add_argument('--engine');c.add_argument('--offset',type=int,default=0);c.add_argument('--limit',type=int,default=100)
 c=sub.add_parser('inspect');c.add_argument('skill_id')
 c=sub.add_parser('invoke');c.add_argument('skill_id');c.add_argument('--project-id');c.add_argument('--workspace-id');c.add_argument('--engine');c.add_argument('--expected-revision',type=int);c.add_argument('--overwrite',action='store_true');c.add_argument('--idempotency-key')
 a=p.parse_args(argv)
 if a.command=='capabilities':return request('GET','/capabilities')
 if a.command=='list':return request('GET','?'+urlencode({k:v for k,v in vars(a).items() if k!='command' and v is not None}))
 if a.command=='inspect':return request('GET','/'+quote(a.skill_id,safe=''))
 raw=sys.stdin.buffer.read(1048577)
 if len(raw)>1048576:raise ValueError('input_too_large')
 payload={'skill_id':a.skill_id,'input':json.loads(raw)};ctx={k:getattr(a,k) for k in ('project_id','workspace_id','engine','expected_revision') if getattr(a,k) is not None}
 if a.overwrite:ctx['overwrite']=True
 if ctx:payload['context']=ctx
 return request('POST','/invoke',payload,a.idempotency_key)
if __name__=='__main__':
 try:
  out=main();print(json.dumps(out,ensure_ascii=False));sys.exit(2 if out.get('error') else 0)
 except Exception as e:
  code=str(e) if isinstance(e,ValueError) and str(e).replace('_','').isalnum() else 'catalog_client_failed';print(json.dumps({'error':{'code':code}}),file=sys.stderr);sys.exit(2)
