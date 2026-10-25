"""Harmonika private full-stack projects. API keys never enter argv or output."""
import argparse,base64,json,os,re,stat,sys,urllib.request,urllib.error
from pathlib import Path
from uuid import UUID,uuid4
BASE='https://chat.harmonika.id/v1/builder'
class NoRedirect(urllib.request.HTTPRedirectHandler):
 def redirect_request(self,*args,**kwargs):raise ValueError('redirect_refused')
def call(method,path,payload=None,key=None,binary=False):
 fd=os.open(Path.home()/'.config/harmonika/harmonika-api-key',os.O_RDONLY|getattr(os,'O_NOFOLLOW',0)|getattr(os,'O_NONBLOCK',0))
 with os.fdopen(fd) as f:
  info=os.fstat(f.fileno())
  if not stat.S_ISREG(info.st_mode) or info.st_uid!=os.getuid() or info.st_mode&0o077:raise ValueError('unsafe_key_file')
  token=f.read(256).strip()
 if not token.startswith('hai_') or len(token)>200:raise ValueError('invalid_key_file')
 body=json.dumps(payload,ensure_ascii=False).encode() if payload is not None else None
 if body and len(body)>1048576:raise ValueError('input_too_large')
 headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'}
 if key:headers['Idempotency-Key']=key
 req=urllib.request.Request(BASE+path,data=body,method=method,headers=headers)
 try:
  with urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect()).open(req,timeout=195) as f:
   raw=f.read(24*1024*1024+1 if binary else 4*1024*1024+1)
   if binary:
    if f.headers.get_content_type()!='application/zip' or len(raw)>24*1024*1024:raise ValueError('invalid_artifact')
    return raw
 except urllib.error.HTTPError as e:
  raw=e.read(65536)
  if binary:raise ValueError('artifact_download_failed') from None
 if len(raw)>4*1024*1024:raise ValueError('response_too_large')
 return json.loads(raw)
def main(argv=None):
 argv=list(sys.argv[1:] if argv is None else argv)
 if argv[:2]==['build','from']:argv.pop(1)
 p=argparse.ArgumentParser(description=__doc__);sub=p.add_subparsers(dest='command',required=True)
 sub.add_parser('capabilities');sub.add_parser('list');c=sub.add_parser('create');c.add_argument('name')
 for name in ('project','download'):
  c=sub.add_parser(name);c.add_argument('project_id')
  if name=='download':c.add_argument('destination')
 c=sub.add_parser('build');c.add_argument('spec_file');c.add_argument('--stack',default='auto');c.add_argument('--name');c.add_argument('--run-checks',action='store_true');c.add_argument('--idempotency-key')
 c=sub.add_parser('deploy');c.add_argument('project_id');c.add_argument('--target',required=True);c.add_argument('--environment',choices=('preview','production'),default='preview');c.add_argument('--expected-revision',type=int,required=True);c.add_argument('--idempotency-key')
 c=sub.add_parser('invoke');c.add_argument('project_id');c.add_argument('skill_id');c.add_argument('--idempotency-key');c.add_argument('--expected-revision',type=int);c.add_argument('--overwrite',action='store_true')
 c=sub.add_parser('upload');c.add_argument('project_id');c.add_argument('source');c.add_argument('--path');c.add_argument('--expected-revision',type=int,required=True)
 for name in ('status','events','stop','approve','deployment'):
  c=sub.add_parser(name);c.add_argument('job_id')
  if name=='events':c.add_argument('--after-sequence',type=int,default=0)
  if name=='approve':c.add_argument('--input-hash',required=True);c.add_argument('--confirm',action='store_true',required=True)
 a=p.parse_args(argv)
 for k in ('project_id','job_id'):
  if hasattr(a,k) and str(UUID(getattr(a,k)))!=getattr(a,k):raise ValueError('invalid_identifier')
 if a.command=='capabilities':return call('GET','/capabilities')
 if a.command=='list':return call('GET','/projects')
 if a.command=='create':return call('POST','/projects',{'name':a.name},str(uuid4()))
 if a.command=='build':
  raw=Path(a.spec_file).read_bytes()
  if len(raw)>50000:raise ValueError('spec_too_large')
  name=a.name or re.sub(r'[^a-z0-9]+','-',Path(a.spec_file).stem.lower()).strip('-')[:50] or 'web-project'
  if not name[0].isalpha() or len(name)<2:name='web-'+name[:45]
  key=a.idempotency_key or str(uuid4());created=call('POST','/projects',{'name':name},key+'-project')
  if created.get('error'):return created
  pid=created['project']['id'];out=call('POST','/projects/'+pid+'/build',{'spec':raw.decode(),'stack':a.stack,'name':name,'run_checks':a.run_checks},key);return {'project_id':pid,'client_idempotency_key':key,**out}
 if a.command=='deploy':return call('POST','/projects/'+a.project_id+'/deploy',{'target':a.target,'environment':a.environment,'expected_revision':a.expected_revision},a.idempotency_key or str(uuid4()))
 if a.command=='project':return call('GET','/projects/'+a.project_id)
 if a.command=='download':
  raw=call('GET','/projects/'+a.project_id+'/artifact',binary=True)
  with open(a.destination,'xb') as f:os.chmod(a.destination,0o600);f.write(raw)
  return {'saved':str(Path(a.destination).resolve()),'size_bytes':len(raw)}
 if a.command=='upload':
  raw=Path(a.source).read_bytes()
  if len(raw)>524288:raise ValueError('file_too_large')
  return call('POST','/projects/'+a.project_id+'/files',{'path':a.path or Path(a.source).name,'bytes_base64':base64.b64encode(raw).decode(),'expected_revision':a.expected_revision},str(uuid4()))
 if a.command=='invoke':
  raw=sys.stdin.buffer.read(1048577)
  if len(raw)>1048576:raise ValueError('input_too_large')
  out=call('POST','/projects/'+a.project_id+'/invoke',{'skill_id':a.skill_id,'input':json.loads(raw),'expected_revision':a.expected_revision,'overwrite':a.overwrite},a.idempotency_key or str(uuid4()));return out
 path='/jobs/'+a.job_id
 if a.command=='status':return call('GET',path)
 if a.command=='deployment':return call('GET',path+'/deployment')
 if a.command=='stop':return call('POST',path+'/stop',{})
 if a.command=='approve':return call('POST',path+'/approve',{'decision':'approve','input_hash':a.input_hash})
 if a.after_sequence<0:raise ValueError('invalid_sequence')
 return call('GET',path+'/events?after_sequence='+str(a.after_sequence))
if __name__=='__main__':
 try:
  v=main();print(json.dumps(v,ensure_ascii=False));sys.exit(2 if v.get('error') else 0)
 except Exception as e:
  code=str(e) if isinstance(e,ValueError) and str(e).replace('_','').isalnum() else 'builder_client_failed'
  print(json.dumps({'error':{'code':code}}),file=sys.stderr);sys.exit(2)
