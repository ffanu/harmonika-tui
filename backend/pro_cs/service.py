"""Private per-actor draft engine. No customer sending, financial changes, or raw-chat logs."""
import hashlib,hmac,json,os,re,sqlite3,subprocess,threading,time,tempfile
from pathlib import Path
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import core,catalog
ROOT=Path(__file__).resolve().parent;STATE=Path('/var/lib/harmonika-pro-cs');TOKEN=Path('/etc/harmonika-ai/pro-cs.token');KEY=Path('/etc/harmonika-ai/pro-cs.scope-key');ID=re.compile(r'^[A-Za-z0-9_-]{1,100}$');SLOTS=threading.BoundedSemaphore(4);FILE_SLOT=threading.BoundedSemaphore(1)
class State:
 def __init__(self,path,key):
  self.key=key;self.lock=threading.RLock();self.db=sqlite3.connect(path,check_same_thread=False);self.db.execute('PRAGMA journal_mode=WAL');self.db.execute('CREATE TABLE IF NOT EXISTS sessions(scope TEXT PRIMARY KEY,revision INTEGER,digest TEXT,takeover INTEGER,closed INTEGER,expires REAL)');self.db.commit()
 def scope(self,actor,body):
  values=[actor]
  for k in ['conversation_id','customer_id','issue_id']:
   value=body.get(k)
   if not isinstance(value,str) or not ID.fullmatch(value):raise core.Error('stable_identity_required')
   values.append(value)
  return hmac.new(self.key,json.dumps(values,separators=(',',':')).encode(),hashlib.sha256).hexdigest()
 def check(self,actor,body,update=False):
  scope=self.scope(actor,body);context=body.get('context');core.context(context);revision=context.get('revision',1)
  digest=hmac.new(self.key,json.dumps(context,sort_keys=True,separators=(',',':'),allow_nan=False).encode(),hashlib.sha256).hexdigest()
  with self.lock:
   self.db.execute('DELETE FROM sessions WHERE expires<?',(time.time(),));row=self.db.execute('SELECT revision,digest,takeover,closed FROM sessions WHERE scope=?',(scope,)).fetchone()
   if row and (revision<row[0] or revision==row[0] and not hmac.compare_digest(row[1],digest)):raise core.Error('stale_revision_or_conflict')
   if not update and (not row or revision!=row[0] or not hmac.compare_digest(row[1],digest)):raise core.Error('draft_not_current')
   if update:
    if row:self.db.execute('UPDATE sessions SET revision=?,digest=?,expires=? WHERE scope=?',(revision,digest,time.time()+86400,scope))
    else:self.db.execute('INSERT INTO sessions VALUES(?,?,?,0,0,?)',(scope,revision,digest,time.time()+86400))
    self.db.commit()
   return {'takeover':bool(row and row[2]),'closed':bool(row and row[3]),'scope':scope}
 def transition(self,actor,body,field):
  scope=self.scope(actor,body);flag=body.get('enabled')
  if type(flag) is not bool:raise core.Error('invalid_state')
  with self.lock:
   row=self.db.execute('SELECT revision FROM sessions WHERE scope=?',(scope,)).fetchone()
   if not row:raise core.Error('unknown_session')
   self.db.execute('UPDATE sessions SET '+field+'=?,expires=? WHERE scope=?',(int(flag),time.time()+86400,scope));self.db.commit()
  return {'ok':True,'status':'completed','auto_send':False,'state':field,'enabled':flag}
def execute(sid,value):
 if sid in catalog.READERS:
  raw=json.dumps(value,allow_nan=False).encode()
  if len(raw)>12*1024*1024:raise core.Error('upload_limit')
  if not FILE_SLOT.acquire(False):return {'status':'degraded','reason':'file_worker_busy','analysis_complete':False}
  try:
   with tempfile.TemporaryDirectory(prefix='harmonika-pro-cs-parser-') as temporary:
    p=subprocess.run(['/opt/harmonika-ai/toolchain-runtime/venv/bin/python','-I',str(ROOT/'readers.py')],input=raw,capture_output=True,timeout=25,env={'PATH':'/usr/bin:/bin','OMP_NUM_THREADS':'1','CUDA_VISIBLE_DEVICES':'','TMPDIR':temporary})
  finally:FILE_SLOT.release()
  if p.returncode or len(p.stdout)>128000:return {'status':'degraded','reason':'file_worker_unavailable','analysis_complete':False}
  return json.loads(p.stdout)
 op=catalog.CORE.get(sid)
 if op=='detect':return core.detect(value.get('text'))
 if op=='plan':return core.plan(value)
 if op=='validate':return core.validate(value)
 if op=='redact':return {'status':'completed','text':core.redact(value.get('text'))}
 if op in ['amount','date','contact','location']:return core.facts({**value,'kind':op})
 row=next((r for r in catalog.entries() if r['id']==sid),None)
 return {'status':'unavailable','reason':row['reason'] if row else 'unknown_skill','auto_send':False,'performed':False,'approval_required':sid.startswith('action.'),'connector_ready':False}
def main():
 STATE.mkdir(parents=True,exist_ok=True,mode=0o700);state=State(STATE/'revisions.sqlite3',bytes.fromhex(KEY.read_text().strip()));token=TOKEN.read_text().strip()
 class Handler(BaseHTTPRequestHandler):
  timeout=15
  def log_message(self,*args):pass
  def reply(self,code,data):
   raw=json.dumps(data,ensure_ascii=False,allow_nan=False,separators=(',',':')).encode();self.send_response(code);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(raw)));self.send_header('Cache-Control','private,no-store');self.end_headers();self.wfile.write(raw)
  def do_POST(self):
   actor=self.headers.get('X-Harmonika-Principal','')
   if not hmac.compare_digest(self.headers.get('X-Pro-CS-Secret',''),token) or not re.fullmatch(r'owner:[a-f0-9]{64}',actor):return self.reply(403,{'ok':False,'error':{'code':'forbidden'}})
   if not SLOTS.acquire(False):return self.reply(429,{'ok':False,'error':{'code':'engine_busy'}})
   try:
    size=int(self.headers.get('Content-Length','0'))
    if not 0<size<=12*1024*1024:raise core.Error('input_limit')
    body=json.loads(self.rfile.read(size),parse_constant=lambda x:(_ for _ in ()).throw(core.Error('invalid_json')))
    if type(body) is not dict:raise core.Error('invalid_request')
    if self.path=='/capabilities':
     if body:raise core.Error('invalid_request')
     rows=catalog.entries();out={'ok':True,'catalog_count':len(rows),'implemented_count':sum(r['implemented'] for r in rows),'skills':rows,'auto_send':False,'max_upload_bytes':8*1024*1024,'long_term_memory':'consent_adapter_required','training_enabled':False,'readiness_is_not_model_quality':True}
    elif self.path=='/invoke':
     if set(body)!={'skill_id','input'} or not isinstance(body['skill_id'],str) or type(body['input']) is not dict:raise core.Error('invalid_request')
     out={'ok':True,'result':execute(body['skill_id'],body['input']),'auto_send':False}
    elif self.path in ['/plan','/validate']:
     allowed={'conversation_id','customer_id','issue_id','context'}|({'draft','draft_revision','source_ids'} if self.path=='/validate' else set())
     if set(body)-allowed:raise core.Error('invalid_request')
     flags=state.check(actor,body,self.path=='/plan')
     if flags['takeover'] or flags['closed']:out={'ok':True,'status':'completed','needs_reply':False,'auto_send':False,'reason':'human_takeover_or_closed'}
     elif self.path=='/plan':out={'ok':True,**core.plan(body['context'])}
     else:out={'ok':True,**core.validate({'context':body['context'],'draft':body.get('draft'),'draft_revision':body.get('draft_revision'),'revision':body['context'].get('revision',1),'source_ids':body.get('source_ids',[])})}
     # A newer burst/takeover arriving during processing invalidates this result.
     latest=state.check(actor,body,False)
     if latest['takeover'] or latest['closed']:out={'ok':True,'status':'completed','needs_reply':False,'auto_send':False,'reason':'human_takeover_or_closed'}
    elif self.path in ['/takeover','/close']:
     if set(body)-{'conversation_id','customer_id','issue_id','enabled'}:raise core.Error('invalid_request')
     out=state.transition(actor,body,'takeover' if self.path=='/takeover' else 'closed')
    else:raise core.Error('unknown_operation')
    return self.reply(200,out)
   except Exception as e:
    code=str(e) if isinstance(e,core.Error) else 'engine_unavailable'
    if not re.fullmatch('[a-z_]{1,90}',code):code='engine_unavailable'
    return self.reply(409 if code in ['stale_revision_or_conflict','draft_not_current'] else 400,{'ok':False,'error':{'code':code}})
   finally:SLOTS.release()
 class Server(ThreadingHTTPServer):
  daemon_threads=True
  def handle_error(self,*args):pass
 Server(('127.0.0.1',18321),Handler).serve_forever()
if __name__=='__main__':main()
