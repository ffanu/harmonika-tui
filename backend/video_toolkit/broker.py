"""Authenticated operator-only video toolkit broker. No public/paid API routing.
Input content is RAM/temporary-job only; SQLite and logs are mechanical metadata.
"""
import base64,hashlib,hmac,importlib.util,io,json,os,queue,re,secrets,sqlite3,subprocess,threading,time
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
CONTROL=Path(__file__).resolve().parent;ROOT=Path(r'D:\HarmonikaAI\video-toolkit');ENGINES=Path(r'C:\HarmonikaVideo')
IDS=re.compile(r'^[a-f0-9]{32}$');OWNERS=re.compile(r'^[a-f0-9]{64}$')
KINDS={'video.generate':'wangp','video.animate_image':'framepack'}
JOBS=Path(r'C:\HarmonikaVideo\jobs')
def read(name):
 try:return json.loads((CONTROL/name).read_text())
 except (OSError,ValueError):return {}
def capabilities():
 result={}
 for name in ['wangp','framepack','mmaudio']:
  installed=read(name+'-install.json').get('phase')=='dependencies_installed';models=read(name+'-models.json').get('models_sha256_verified',False);verified=read(name+'-qualification.json').get('phase')=='verified'
  result[name]={'installed':installed,'models_verified':models,'inference_verified':verified,'operator_ready':installed and models and verified and name!='mmaudio','research_cli_ready':installed and models and verified if name=='mmaudio' else False,'public_route_enabled':False,'commercial_enabled':False if name=='mmaudio' else None,'qualified_duration_seconds':[1] if name!='mmaudio' and verified else [8] if verified else []}
 return {'service':'Harmonika Ai','audience':'private_operator_only','engines':result}
def validate(payload):
 if not isinstance(payload,dict) or set(payload)-{'kind','prompt','duration_seconds','image_base64'}:raise ValueError('invalid_input')
 kind=payload.get('kind');prompt=payload.get('prompt');seconds=payload.get('duration_seconds',1)
 if kind=='audio.soundtrack':raise ValueError('license_restricted')
 if kind not in KINDS or not isinstance(prompt,str) or not prompt.strip() or len(prompt.encode())>8000 or type(seconds) is not int or seconds!=1:raise ValueError('unsupported_profile')
 if kind=='video.animate_image':
  image=payload.get('image_base64')
  if not isinstance(image,str) or len(image)>12*1024*1024:raise ValueError('invalid_image')
  try:raw=base64.b64decode(image,validate=True)
  except (ValueError,TypeError):raise ValueError('invalid_image') from None
  if not 100<=len(raw)<=8*1024*1024:raise ValueError('invalid_image')
  if not (raw.startswith(b'\x89PNG\r\n\x1a\n') or raw.startswith(b'\xff\xd8\xff') or (raw.startswith(b'RIFF') and raw[8:12]==b'WEBP')):raise ValueError('invalid_image')
 elif payload.get('image_base64'):raise ValueError('unsupported_reference')
 return payload
class Broker:
 def __init__(self):
  self.lock=threading.RLock();self.pending=queue.Queue(maxsize=4)
  self.db=sqlite3.connect(CONTROL/'broker.sqlite3',check_same_thread=False);self.db.row_factory=sqlite3.Row;self.db.execute('PRAGMA journal_mode=WAL')
  self.db.execute('CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY,owner TEXT,idem TEXT,digest TEXT,state TEXT,created REAL,updated REAL,error_type TEXT,latency REAL,result TEXT,UNIQUE(owner,idem))')
  self.db.execute("UPDATE jobs SET state='unknown',error_type='broker_restarted' WHERE state IN ('queued','running')");self.db.commit()
 def row(self,owner,jid):
  with self.lock:
   row=self.db.execute('SELECT * FROM jobs WHERE owner=? AND id=?',(owner,jid)).fetchone();return dict(row) if row else None
 def public(self,row):
  return {k:row[k] for k in ['id','state','error_type','latency']}|{'result':json.loads(row['result']) if row['result'] else None}
 def submit(self,owner,idem,payload):
  data=validate(payload);engine=KINDS[data['kind']];digest=hashlib.sha256(json.dumps(data,sort_keys=True).encode()).hexdigest()
  with self.lock:
   old=self.db.execute('SELECT * FROM jobs WHERE owner=? AND idem=?',(owner,idem)).fetchone()
   if old:
    if old['digest']!=digest:raise ValueError('idempotency_conflict')
    return dict(old)
   if not capabilities()['engines'][engine]['operator_ready']:raise ValueError('engine_not_qualified')
   if self.db.execute("SELECT 1 FROM jobs WHERE state='unknown'").fetchone():raise ValueError('engine_recovery_required')
   if self.db.execute('SELECT count(*) FROM jobs').fetchone()[0]>=1000:raise ValueError('storage_full')
   if self.pending.full() or self.db.execute("SELECT 1 FROM jobs WHERE owner=? AND state IN ('queued','running')",(owner,)).fetchone():raise ValueError('queue_full')
   if self.db.execute('SELECT count(*) FROM jobs WHERE owner=? AND created>?',(owner,time.time()-86400)).fetchone()[0]>=10:raise ValueError('daily_limit')
   jid=secrets.token_hex(16);now=time.time();self.db.execute('INSERT INTO jobs VALUES(?,?,?,?,?,?,?,?,?,?)',(jid,owner,idem,digest,'queued',now,now,None,None,None));self.db.commit();self.pending.put_nowait((jid,data));return self.row(owner,jid)
 def worker(self):
  while True:
   jid,data=self.pending.get();start=time.monotonic();job=JOBS/jid
   engine=KINDS[data['kind']];child=None;containment=None;guard=None;native_error=None
   with self.lock:self.db.execute("UPDATE jobs SET state='running',updated=? WHERE id=?",(time.time(),jid));self.db.commit()
   try:
    job.mkdir(parents=True)
    import msvcrt
    guard=(CONTROL/'gpu.lock').open('a+b');guard.seek(0);guard.write(b'0');guard.flush();guard.seek(0)
    try:msvcrt.locking(guard.fileno(),msvcrt.LK_NBLCK,1)
    except OSError:raise BlockingIOError('gpu_busy') from None
    payload={'prompt':data['prompt'],'duration_seconds':1,'steps':25 if engine=='framepack' else 50}
    (job/'input.json').write_text(json.dumps(payload))
    if engine=='framepack':
     raw=base64.b64decode(data['image_base64'],validate=True)
     assert raw.startswith(b'\x89PNG\r\n\x1a\n') or raw.startswith(b'\xff\xd8\xff') or (raw.startswith(b'RIFF') and raw[8:12]==b'WEBP')
     (job/'input.png').write_bytes(raw)
    env={k:v for k,v in os.environ.items() if k not in ['PYTHONPATH','PYTHONHOME','PYTHONSTARTUP','HF_TOKEN'] and not any(x in k.upper() for x in ['API_KEY','ACCESS_TOKEN','PASSWORD','SECRET'])};env.update(HF_HUB_OFFLINE='1',DO_NOT_TRACK='1',HARMONIKA_MMAUDIO_RESEARCH='0')
    spec=importlib.util.spec_from_file_location('owned_job',CONTROL/'owned_job.py');module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    child=subprocess.Popen([str(ENGINES/engine/'.venv/Scripts/python.exe'),'-I',str(CONTROL/(engine+'_runner.py')),str(job)],cwd=CONTROL,env=env,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=0x4);containment=module.WindowsJob(child)
    while child.poll() is None:
     if time.monotonic()-start>2400:raise TimeoutError()
     time.sleep(2)
    result=json.loads((job/'result.json').read_text())
    if result.get('status')!='completed':
     candidate=result.get('error_code');native_error=candidate if candidate in ['gpu_busy','system_memory_low','insufficient_vram'] else None
     raise RuntimeError('inference_failed')
    state,error='completed',None
   except Exception as exc:result=None;state='failed';error=native_error or ('gpu_busy' if isinstance(exc,BlockingIOError) else 'engine_timeout' if isinstance(exc,TimeoutError) else 'engine_failed')
   finally:
    if containment:containment.close()
    if child is not None and child.poll() is None:child.kill();child.wait(10)
    if guard:guard.close()
    try:
     for name in ['input.json','input.png']:(job/name).unlink(missing_ok=True)
     import shutil
     if (job/'native').exists():shutil.rmtree(job/'native')
    except OSError:state='failed';error='input_cleanup_failed';result=None
   with self.lock:self.db.execute('UPDATE jobs SET state=?,updated=?,error_type=?,latency=?,result=? WHERE id=?',(state,time.time(),error,round(time.monotonic()-start,2),json.dumps(result) if result else None,jid));self.db.commit()
   print(json.dumps({'event':'toolkit_job_terminal','state':state,'error_type':error,'latency':round(time.monotonic()-start,2)}),flush=True);self.pending.task_done()
def main():
 import msvcrt
 guard=(CONTROL/'broker.lock').open('a+b');guard.seek(0);guard.write(b'0');guard.flush();guard.seek(0)
 try:msvcrt.locking(guard.fileno(),msvcrt.LK_NBLCK,1)
 except OSError:
  guard.close();print(json.dumps({'event':'toolkit_broker_already_running'}),flush=True);return
 token=(CONTROL/'service.token').read_text().strip();assert len(token)>=40
 class Handler(BaseHTTPRequestHandler):
  timeout=20
  def log_message(self,*args):pass
  def json(self,status,data):
   body=json.dumps(data).encode();self.send_response(status);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(body)));self.send_header('Cache-Control','no-store');self.end_headers();self.wfile.write(body)
  def auth(self):
   owner=self.headers.get('X-Resource-Owner','');auth=self.headers.get('Authorization','').encode()
   if not OWNERS.fullmatch(owner) or not hmac.compare_digest(auth,('Bearer '+token).encode()):self.json(401,{'error':'unauthorized'});return None
   return owner
  def do_GET(self):
   owner=self.auth()
   if owner is None:return
   if self.path in ['/health','/capabilities']:return self.json(200,capabilities())
   lookup=re.fullmatch('/requests/([a-f0-9]{32})',self.path)
   if lookup:
    with broker.lock:row=broker.db.execute('SELECT * FROM jobs WHERE owner=? AND idem=?',(owner,lookup[1])).fetchone()
    return self.json(200,broker.public(dict(row))) if row else self.json(404,{'error':'not_found'})
   match=re.fullmatch('/jobs/([a-f0-9]{32})(/file)?',self.path);row=broker.row(owner,match[1]) if match else None
   if not row:return self.json(404,{'error':'not_found'})
   if not match[2]:return self.json(200,broker.public(row))
   path=JOBS/row['id']/'result.mp4'
   if row['state']!='completed' or not path.is_file():return self.json(404,{'error':'not_found'})
   try:file=path.open('rb')
   except OSError:return self.json(503,{'error':'artifact_unavailable'})
   with file:
    self.send_response(200);self.send_header('Content-Type','video/mp4');self.send_header('Content-Length',str(path.stat().st_size));self.end_headers()
    while chunk:=file.read(65536):self.wfile.write(chunk)
  def do_POST(self):
   owner=self.auth()
   if owner is None:return
   if self.path!='/jobs':return self.json(404,{'error':'not_found'})
   try:
    length=int(self.headers.get('Content-Length','0'));idem=self.headers.get('Idempotency-Key','');assert 0<length<=12*1024*1024 and IDS.fullmatch(idem)
    row=broker.submit(owner,idem,json.loads(self.rfile.read(length)));self.json(202,broker.public(row))
   except (ValueError,AssertionError,OSError) as exc:
    error=str(exc) if str(exc) in ['license_restricted','unsupported_profile','invalid_image','unsupported_reference','invalid_input','idempotency_conflict','engine_not_qualified','engine_recovery_required','queue_full','daily_limit','storage_full'] else 'invalid_input';self.json(409 if error=='idempotency_conflict' else 400,{'error':error})
 class Server(ThreadingHTTPServer):
  daemon_threads=True
  def handle_error(self,*args):print(json.dumps({'event':'toolkit_http_error','error_type':'handler_failed'}),flush=True)
 server=Server(('127.0.0.1',18237),Handler)
 try:
  broker=Broker();threading.Thread(target=broker.worker,daemon=True).start();server.serve_forever()
 finally:server.server_close();guard.close()
if __name__=='__main__':main()
