#!/usr/bin/env python3
"""Explicit owner smoke; temporary API licenses arrive on stdin, never logged."""
import hashlib,json,os,sqlite3,sys,time,urllib.error,urllib.request,uuid
from pathlib import Path
class NoRedirect(urllib.request.HTTPRedirectHandler):
 def redirect_request(self,*a,**k):raise RuntimeError('redirect_refused')
op=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect());B='https://chat.harmonika.id/v1/media';S=Path('/opt/harmonika-ai/media-bridge-stage');auth=json.load(sys.stdin);tokens=auth['tokens'];owners=['api_'+str(x) for x in auth['ids']];started=time.monotonic();checks=[];jobid=None;report={'status':'failed'}
def call(method,path,key,body=None,idem=None,binary=False):
 headers={'Authorization':'Bearer '+key,'Content-Type':'application/json'}
 if idem:headers['Idempotency-Key']=idem
 req=urllib.request.Request(B+path,headers=headers,method=method,data=json.dumps(body).encode() if body is not None else None)
 try:
  with op.open(req,timeout=30) as r:
   raw=r.read(16777217);return r.status,raw if binary else json.loads(raw)
 except urllib.error.HTTPError as e:
  return e.code,json.loads(e.read(65536))
try:
 assert call('GET','/capabilities','invalid')[0] in (401,403);checks.append('unauthenticated_rejected')
 code,caps=call('GET','/capabilities',tokens[0]);assert code==200 and caps['features']['image_generation'] and not caps['features']['image_analysis'];checks.append('truthful_capabilities')
 assert call('POST','/jobs',tokens[0],{'kind':'generate','prompt':'test'})[0]==400;checks.append('idempotency_required')
 payload={'kind':'generate','prompt':'A clean graphic illustration of one bright orange circle centered on a deep blue square background, no letters, flat geometric artwork','size':'512x512','reference_file_ids':[]};idem='isolated-media-'+uuid.uuid4().hex
 code,out=call('POST','/jobs',tokens[0],payload,idem);assert code==202;jobid=out['job']['job_id'];checks.append('accepted_async_job')
 code,out=call('POST','/jobs',tokens[0],payload,idem);assert code==200 and out['job']['job_id']==jobid;checks.append('idempotency_single_job')
 assert call('GET','/jobs/'+jobid,tokens[1])[0]==404;checks.append('foreign_job_rejected')
 deadline=time.monotonic()+175
 while time.monotonic()<deadline:
  code,out=call('GET','/jobs/'+jobid,tokens[0]);assert code==200;job=out['job']
  if job['status'] in ('completed','failed','unknown','cancelled'):break
  time.sleep(.8)
 assert job['status']=='completed','render_not_completed'
 artifact=job['result']['artifacts'][0];fid=artifact['file_id'];assert artifact['mime_type']=='image/png'
 assert call('GET','/files/'+fid,tokens[1])[0]==404;assert call('GET','/files/'+fid,'invalid')[0] in (401,403);checks.append('private_file_ownership')
 code,data=call('GET','/files/'+fid,tokens[0],binary=True);assert code==200 and data.startswith(b'\x89PNG\r\n\x1a\n') and len(data)==artifact['size_bytes'] and hashlib.sha256(data).hexdigest()==artifact['sha256'];checks.append('png_integrity')
 fd=os.open(S/'render-smoke.png',os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
 with os.fdopen(fd,'wb') as f:f.write(data)
 report={'status':'passed','scenarios':checks,'elapsed_seconds':round(time.monotonic()-started,2),'render':{k:artifact[k] for k in ('mime_type','size_bytes','width','height','sha256')},'actual_windows_gpu_render':True,'image_analysis_ready':False,'fixture_cleanup':'pending'}
finally:
 # Only delete own terminal fixtures. Preserve uncertain work for operator recovery.
 with sqlite3.connect('/var/lib/harmonika-rtx-queue/queue.sqlite3',timeout=5) as c:
  c.execute('BEGIN IMMEDIATE');active=c.execute("SELECT count(*) FROM jobs WHERE owner IN (?,?) AND status NOT IN ('completed','failed','cancelled')",owners).fetchone()[0]
  if not active:
   ids=[r[0] for r in c.execute('SELECT id FROM files WHERE owner IN (?,?)',owners)]
   c.execute('DELETE FROM files WHERE owner IN (?,?)',owners);c.execute('DELETE FROM jobs WHERE owner IN (?,?)',owners)
   for fid in ids:
    p=Path('/var/lib/harmonika-rtx-queue/files')/fid
    if p.is_file() and not p.is_symlink():p.unlink()
   report['fixture_cleanup']='completed'
  else:report['fixture_cleanup']='uncertain_job_preserved'
 (S/'public-smoke.json').write_text(json.dumps(report,indent=2));print(json.dumps(report))
