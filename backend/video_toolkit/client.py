"""PC1 operator-only adapter. Stable internal interface, no vendor SDK in TUI/UI."""
import argparse,base64,hashlib,hmac,json,os,re,secrets,urllib.request,urllib.error
from pathlib import Path
BASE='http://127.0.0.1:18240';TOKEN=Path('/etc/harmonika-ai/video-toolkit.token')
class Client:
 def __init__(self,session='operator-core'):
  self.token=TOKEN.read_text().strip();self.owner=hmac.new(self.token.encode(),('operator:'+session).encode(),hashlib.sha256).hexdigest()
 def call(self,method,path,payload=None,idem=None):
  headers={'Authorization':'Bearer '+self.token,'X-Resource-Owner':self.owner,'Content-Type':'application/json'}
  if idem:headers['Idempotency-Key']=idem
  request=urllib.request.Request(BASE+path,method=method,headers=headers,data=json.dumps(payload).encode() if payload is not None else None)
  try:
   with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(request,timeout=20) as response:
    data=response.read(65537)
    if len(data)>65536:raise ValueError('response_limit')
    return json.loads(data)
  except urllib.error.HTTPError as exc:
   try:code=json.loads(exc.read(8192)).get('error','engine_unavailable')
   except ValueError:code='engine_unavailable'
   return {'status':'degraded','error':code}
  except (OSError,ValueError):return {'status':'degraded','error':'engine_unavailable','submission_uncertain':method=='POST'}
 def capabilities(self):return self.call('GET','/capabilities')
 def submit(self,kind,prompt,image=None,request_id=None):
  payload={'kind':kind,'prompt':prompt,'duration_seconds':1}
  if image is not None:
   if len(image)>8*1024*1024:raise ValueError('image_limit')
   payload['image_base64']=base64.b64encode(image).decode()
  request_id=request_id or secrets.token_hex(16)
  result=self.call('POST','/jobs',payload,request_id)
  if result.get('submission_uncertain'):
   recovered=self.call('GET','/requests/'+request_id)
   if recovered.get('id'):result=recovered
  result['request_id']=request_id
  return result
 def download(self,job_id,target):
  state=self.job(job_id);result=state.get('result') or {};expected=result.get('sha256','')
  if state.get('state')!='completed' or not re.fullmatch('[a-f0-9]{64}',expected):return {'status':'degraded','error':'artifact_not_ready'}
  target=Path(target)
  if target.exists():return {'status':'degraded','error':'artifact_destination_exists'}
  target.parent.mkdir(parents=True,exist_ok=True);temp=target.with_name(target.name+'.'+secrets.token_hex(8)+'.partial')
  request=urllib.request.Request(BASE+'/jobs/'+job_id+'/file',headers={'Authorization':'Bearer '+self.token,'X-Resource-Owner':self.owner})
  try:
   with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(request,timeout=20) as response:
    length=int(response.headers.get('Content-Length','0'))
    if not 0<length<=20*1024*1024 or response.headers.get('Content-Type')!='video/mp4':raise ValueError('invalid_artifact')
    digest=hashlib.sha256();size=0
    with temp.open('xb') as out:
     temp.chmod(0o600)
     while chunk:=response.read(65536):
      size+=len(chunk)
      if size>length:raise ValueError('artifact_limit')
      digest.update(chunk);out.write(chunk)
   if size!=length or digest.hexdigest()!=expected:raise ValueError('artifact_integrity_failed')
   # Linux operator client: link publishes atomically without overwriting a file.
   os.link(temp,target)
   return {'status':'completed','bytes':size,'sha256':expected,'path':str(target)}
  except (OSError,ValueError):return {'status':'degraded','error':'artifact_unavailable'}
  finally:temp.unlink(missing_ok=True)
 def request(self,request_id):
  if not re.fullmatch('[a-f0-9]{32}',request_id):raise ValueError('invalid_request')
  return self.call('GET','/requests/'+request_id)
 def job(self,job_id):
  if not re.fullmatch('[a-f0-9]{32}',job_id):raise ValueError('invalid_job')
  return self.call('GET','/jobs/'+job_id)
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('action',choices=['capabilities','submit','job','artifact']);p.add_argument('--session',default='operator-core');p.add_argument('--kind',choices=['video.generate','video.animate_image']);p.add_argument('--prompt-file');p.add_argument('--image');p.add_argument('--job-id');p.add_argument('--output');args=p.parse_args();c=Client(args.session)
 if args.action=='capabilities':out=c.capabilities()
 elif args.action=='job':out=c.job(args.job_id)
 elif args.action=='artifact':
  assert args.output
  out=c.download(args.job_id,args.output)
 else:
  assert args.kind and args.prompt_file
  out=c.submit(args.kind,Path(args.prompt_file).read_text(),Path(args.image).read_bytes() if args.image else None)
 print(json.dumps(out,indent=2))
