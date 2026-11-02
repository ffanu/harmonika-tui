"""Private, disk-budgeted video duration orchestration. No prompts in SQLite/logs."""
import base64,hashlib,json,math,os,re,secrets,shutil,sqlite3,subprocess,threading,time,urllib.request,urllib.error
from pathlib import Path
IDS=re.compile(r'^[a-f0-9]{32}$')
class Failure(Exception):pass

def validate(data):
 if not isinstance(data,dict) or set(data)-{'kind','prompt','duration_seconds','image_base64'}:raise Failure('invalid_input')
 seconds=data.get('duration_seconds')
 if type(seconds) is not int or not 1<=seconds<=2**31-1:raise Failure('invalid_duration')
 if data.get('kind') not in ('video.generate','video.animate_image'):raise Failure('invalid_input')
 if not isinstance(data.get('prompt'),str) or not data['prompt'].strip() or len(data['prompt'].encode())>8000:raise Failure('invalid_prompt')
 if data['kind']=='video.animate_image':
  try:
   import io
   from PIL import Image
   raw=base64.b64decode(data.get('image_base64',''),validate=True)
   if not 100<=len(raw)<=8*1024*1024:raise ValueError()
   with Image.open(io.BytesIO(raw)) as im:
    if im.format not in ('PNG','JPEG','WEBP') or im.width*im.height>16777216:raise ValueError()
    im.verify()
  except Exception:raise Failure('invalid_image') from None
 return data

def chunks(seconds):
 remaining=seconds
 while remaining:
  span=min(10,remaining);yield span;remaining-=span

def command(args):
 try:
  r=subprocess.run(args,stdin=subprocess.DEVNULL,capture_output=True,timeout=180)
  if r.returncode:raise Failure('video_processing_failed')
  return r.stdout
 except subprocess.TimeoutExpired:raise Failure('video_processing_timeout') from None

def duration(path):
 try:return float(json.loads(command(['ffprobe','-v','error','-show_entries','format=duration','-of','json',str(path)]))['format']['duration'])
 except Exception:raise Failure('invalid_video_artifact') from None

class Engine:
 def __init__(self,root,worker_token,base='http://127.0.0.1:18203'):
  self.root=Path(root);self.root.mkdir(parents=True,exist_ok=True,mode=0o700);self.token=worker_token;self.base=base
  self.lock=threading.RLock();self.db=sqlite3.connect(self.root/'mechanics.sqlite',check_same_thread=False);self.db.row_factory=sqlite3.Row
  self.db.execute('PRAGMA journal_mode=WAL')
  self.db.execute('CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY,owner TEXT,idem TEXT,digest TEXT,state TEXT,error TEXT,seconds INTEGER,progress INTEGER,created REAL,UNIQUE(owner,idem))')
  self.db.execute("UPDATE jobs SET state='unknown',error='restart_reconciliation_required' WHERE state IN ('running','queued')");self.db.commit()
 def row(self,owner,key,request=False):
  with self.lock:
   r=self.db.execute('SELECT * FROM jobs WHERE owner=? AND '+('idem' if request else 'id')+'=?',(owner,key)).fetchone()
  if not r:raise Failure('not_found')
  result={'duration_seconds':r['seconds'],'audio':False}
  path=self.root/r['id']/'result.mp4'
  if r['state']=='completed':
   result=json.loads((path.parent/'result.json').read_text())
  return {'ok':True,'provider':'Harmonika Ai','job_id':r['id'],'state':r['state'],'error_code':r['error'],'result':result,'progress_seconds':r['progress'],'poll_after_seconds':10}
 def update(self,jid,**fields):
  assert set(fields)<= {'state','error','progress'}
  with self.lock:
   self.db.execute('UPDATE jobs SET '+','.join(k+'=?' for k in fields)+' WHERE id=?',(*fields.values(),jid));self.db.commit()
 def submit(self,owner,idem,data):
  validate(data)
  if not re.fullmatch('[a-f0-9]{64}',owner) or not IDS.fullmatch(idem):raise Failure('invalid_request')
  digest=hashlib.sha256(json.dumps(data,sort_keys=True).encode()).hexdigest()
  with self.lock:
   old=self.db.execute('SELECT * FROM jobs WHERE owner=? AND idem=?',(owner,idem)).fetchone()
   if old:
    if old['digest']!=digest:raise Failure('idempotency_conflict')
    return self.row(owner,idem,True)
   # Resource admission instead of an arbitrary 1/10-second product limit.
   if shutil.disk_usage(self.root).free < 2*1024**3+data['duration_seconds']*1024**2:raise Failure('insufficient_storage_for_duration')
   if self.db.execute("SELECT count(*) FROM jobs WHERE state IN ('queued','running','unknown')").fetchone()[0]>=4:raise Failure('queue_full')
   jid='d04a'+secrets.token_hex(14);job=self.root/jid;job.mkdir(mode=0o700)
   p=job/'input.json';fd=os.open(p,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
   with os.fdopen(fd,'w') as f:json.dump(data,f)
   self.db.execute('INSERT INTO jobs VALUES(?,?,?,?,?,?,?,?,?)',(jid,owner,idem,digest,'queued',None,data['duration_seconds'],0,time.time()));self.db.commit()
   return self.row(owner,jid)
 def request(self,method,path,data=None,idem=None,binary=False,dest=None):
  headers={'Authorization':'Bearer '+self.token,'Content-Type':'application/json'}
  if idem:headers['Idempotency-Key']=idem
  req=urllib.request.Request(self.base+path,method=method,headers=headers,data=json.dumps(data).encode() if data is not None else None)
  try:
   with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req,timeout=60) as r:
    if binary:
     total=0
     with dest.open('xb') as f:
      while part:=r.read(512*1024):
       total+=len(part)
       if total>96*1024**2:raise Failure('segment_output_limit')
       f.write(part)
     return
    raw=r.read(1024**2+1)
    if len(raw)>1024**2:raise Failure('worker_payload_limit')
    return json.loads(raw)
  except urllib.error.HTTPError as e:raise Failure('worker_rejected' if e.code!=404 else 'remote_job_lost') from None
  except (OSError,ValueError):raise Failure('submission_uncertain' if method=='POST' else 'worker_offline') from None
 def remote(self,jid,index,skill,inp,job):
  marker=job/('remote-%d.json'%index)
  if marker.exists():rid=json.loads(marker.read_text())['job_id']
  else:
   # A POST timeout is terminal/uncertain: never blindly submit a new GPU job.
   response=self.request('POST','/v1/jobs',{'skill_id':skill,'input':inp},jid+'-'+str(index))
   rid=response.get('job_id')
   if not isinstance(rid,str) or not re.fullmatch('gw_[a-f0-9]{16,32}',rid):raise Failure('invalid_worker_response')
   marker.write_text(json.dumps({'job_id':rid}));marker.chmod(0o600)
  start=time.monotonic()
  while True:
   result=self.request('GET','/v1/jobs/'+rid)
   if result.get('state')=='completed':return result.get('result',{})
   if result.get('state') in ('failed','unknown'):raise Failure('worker_generation_failed')
   if time.monotonic()-start>3600:raise Failure('worker_timeout_uncertain')
   time.sleep(5)
 def run(self,jid):
  job=self.root/jid;self.update(jid,state='running');data=json.loads((job/'input.json').read_text());span_done=0
  try:
   image=data.get('image_base64')
   if image is None:
    r=self.remote(jid,-1,'image.generate',{'prompt':data['prompt'],'width':512,'height':512},job)
    self.download_remote(r,job/'seed.png');image=base64.b64encode((job/'seed.png').read_bytes()).decode()
   manifest=job/'segments.txt'
   with manifest.open('w') as listing:
    for i,span in enumerate(chunks(data['duration_seconds'])):
     if shutil.disk_usage(job).free<1024**3:raise Failure('storage_full')
     result=self.remote(jid,i,'video.generate',{'prompt':data['prompt'],'image_base64':image,'seconds':max(3,span),'width':512,'height':288},job)
     raw=job/'raw.mp4';self.download_remote(result,raw)
     if duration(raw)+.05<span:raise Failure('worker_video_too_short')
     segment=job/('%06d.mp4'%i)
     command(['ffmpeg','-v','error','-y','-i',str(raw),'-t',str(span),'-an','-vf','scale=512:288,fps=16','-c:v','libx264','-preset','fast','-crf','20',str(segment)])
     if abs(duration(segment)-span)>.07:raise Failure('segment_duration_mismatch')
     listing.write("file '%s'\n"%segment.name);listing.flush()
     command(['ffmpeg','-v','error','-y','-sseof','-0.1','-i',str(segment),'-frames:v','1',str(job/'next.png')])
     image=base64.b64encode((job/'next.png').read_bytes()).decode();raw.unlink();span_done+=span;self.update(jid,progress=span_done)
   out=job/'result.mp4'
   command(['ffmpeg','-v','error','-y','-f','concat','-safe','1','-i',str(manifest),'-c','copy','-movflags','+faststart',str(out)])
   actual=duration(out)
   if abs(actual-data['duration_seconds'])>.07:raise Failure('final_duration_mismatch')
   h=hashlib.sha256()
   with out.open('rb') as f:
    while part:=f.read(1024**2):h.update(part)
   (job/'result.json').write_text(json.dumps({'duration_seconds':actual,'frames':round(actual*16),'width':512,'height':288,'audio':False,'sha256':h.hexdigest(),'bytes':out.stat().st_size,'segmented':True,'seamless_continuity_guaranteed':False}))
   self.update(jid,state='completed')
  except Exception as e:
   code=str(e) if isinstance(e,Failure) else 'video_processing_failed'
   self.update(jid,state='unknown' if code in ('submission_uncertain','worker_timeout_uncertain','remote_job_lost') else 'failed',error=code)
  finally:
   for p in job.iterdir():
    if p.name not in ('result.mp4','result.json') and p.is_file():p.unlink(missing_ok=True)
 def download_remote(self,result,dest):
  path=result.get('url') or '/files/'+str(result.get('file',''))
  if not re.fullmatch(r'/files/[A-Za-z0-9_.-]+',path):raise Failure('invalid_worker_artifact')
  self.request('GET',path,binary=True,dest=dest)
 def retention(self):
  with self.lock:
   rows=self.db.execute("SELECT id FROM jobs WHERE state IN ('completed','failed') AND created<?",(time.time()-86400,)).fetchall()
   for row in rows:
    p=self.root/row['id']
    if p.is_dir() and not p.is_symlink():shutil.rmtree(p)
    self.db.execute("UPDATE jobs SET state='failed',error='artifact_expired' WHERE id=?",(row['id'],))
   self.db.commit()
 def worker(self):
  sweep=0
  while True:
   if time.monotonic()-sweep>3600:self.retention();sweep=time.monotonic()
   with self.lock:r=self.db.execute("SELECT id FROM jobs WHERE state='queued' ORDER BY created LIMIT 1").fetchone()
   if r:self.run(r['id'])
   else:time.sleep(1)
