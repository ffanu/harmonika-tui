"""Fixed-target lab TCP fault relay. Technical SQLite only; no bodies/headers logged."""
from contextlib import contextmanager
import hashlib,http.client,json,os,re,signal,socket,sqlite3,threading,time
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
ROOT=Path('/data');RELEASE=os.environ['LAB_RELEASE']
@contextmanager
def db():
 c=sqlite3.connect(ROOT/'network.sqlite',timeout=1,isolation_level=None);c.row_factory=sqlite3.Row
 try:yield c
 finally:c.close()
with db() as c:
 c.execute('PRAGMA journal_mode=WAL');c.executescript('CREATE TABLE IF NOT EXISTS calls(id INTEGER PRIMARY KEY,event TEXT,coordinator_id TEXT,method TEXT,route TEXT,key_sha256 TEXT,job_id TEXT,status INTEGER,latency_ms REAL,at REAL,release TEXT);CREATE TABLE IF NOT EXISTS faults(release TEXT,name TEXT,target TEXT,remaining INTEGER,PRIMARY KEY(release,name,target));')
def audit(event,identity,method,path,key='',job=None,status=0,began=None):
 route=re.sub(r'/(jobs|requests|artifacts)/[^/]+',r'/\1/:id',path)
 with db() as c:c.execute('INSERT INTO calls(event,coordinator_id,method,route,key_sha256,job_id,status,latency_ms,at,release) VALUES(?,?,?,?,?,?,?,?,?,?)',(event,identity,method,route,hashlib.sha256(key.encode()).hexdigest() if key else '',job,status,round((time.monotonic()-began)*1000,3) if began else 0,time.time(),RELEASE))
def take(name,identity):
 with db() as c:
  c.execute('BEGIN IMMEDIATE');r=c.execute('SELECT target FROM faults WHERE release=? AND name=? AND target IN (?,?) AND remaining>0 ORDER BY target LIMIT1'.replace('LIMIT1','LIMIT 1'),(RELEASE,name,identity,'all')).fetchone()
  if r is None:c.rollback();return False
  c.execute('UPDATE faults SET remaining=remaining-1 WHERE release=? AND name=? AND target=?',(RELEASE,name,r['target']));c.commit();return True
class Handler(BaseHTTPRequestHandler):
 protocol_version='HTTP/1.1'
 def setup(self):super().setup();self.connection.settimeout(55)
 def log_message(self,*a):pass
 def close_tcp(self):
  self.close_connection=True
  try:self.connection.shutdown(socket.SHUT_RDWR)
  except OSError:pass
  self.connection.close()
 def respond(self,status,raw):self.send_response(status);self.send_header('Content-Length',str(len(raw)));self.send_header('Content-Type','application/json');self.end_headers();self.wfile.write(raw)
 def do_GET(self):self.route('GET')
 def do_POST(self):self.route('POST')
 def route(self,method):
  path=self.path;identity=self.headers.get('X-Lab-Coordinator','');key=self.headers.get('Idempotency-Key','');owner=self.headers.get('X-Lab-Owner','');began=time.monotonic()
  if path=='/proxy-healthz':return self.respond(200,b'{"ready":true}')
  if identity not in ['api','coordinator1','coordinator2'] or owner not in ['alpha','beta'] or not re.fullmatch(r'/(?:jobs(?:/[a-f0-9]{32}(?:/cancel)?)?|requests/[A-Za-z0-9_-]{1,64}|artifacts/[a-f0-9]{32}|queue-readyz)',path):return self.respond(403,b'{"error":"scope_denied"}')
  if (ROOT/'network-partition').exists():audit('partition_closed',identity,method,path,key,began=began);return self.close_tcp()
  try:size=int(self.headers.get('Content-Length','0'))
  except ValueError:return self.respond(422,b'{"error":"invalid_length"}')
  if not 0<=size<=8192:return self.respond(413,b'{"error":"too_large"}')
  body=self.rfile.read(size) if size else None;c=http.client.HTTPConnection('api',18081,timeout=5)
  try:
   c.request(method,path,body=body,headers={'X-Lab-Owner':owner,'X-Lab-Child':'1','Idempotency-Key':key,'Content-Type':'application/json','Authorization':self.headers.get('Authorization','')});r=c.getresponse();raw=r.read(65537)
   if len(raw)>65536:raise ValueError()
   job=None
   try:job=json.loads(raw).get('job',{}).get('id')
   except (ValueError,AttributeError):pass
   if path.startswith('/artifacts/'):job=path.rsplit('/',1)[1]
   audit('forward',identity,method,path,key,job,r.status,began)
   if method=='POST' and path=='/jobs' and r.status in [200,202]:
    if take('hold-after-submit',identity):
     audit('response_hold',identity,method,path,key,job,r.status,began);end=time.monotonic()+45
     while time.monotonic()<end and not (ROOT/'release-held').exists():time.sleep(.025)
     if not (ROOT/'release-held').exists():audit('hold_timeout',identity,method,path,key,job,r.status,began);return self.close_tcp()
     audit('response_released',identity,method,path,key,job,r.status,began)
    if take('drop-ack',identity):audit('ack_dropped',identity,method,path,key,job,r.status,began);return self.close_tcp()
   if method=='GET' and path.startswith('/artifacts/') and r.status==200 and take('partial-artifact',identity):
    audit('artifact_truncated',identity,method,path,key,job,r.status,began);self.send_response(200);self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw[:max(1,len(raw)//2)]);self.wfile.flush();return self.close_tcp()
   self.respond(r.status,raw)
  except (OSError,http.client.HTTPException,ValueError):
   audit('upstream_unavailable',identity,method,path,key,began=began)
   try:self.respond(503,b'{"error":"child_unavailable"}')
   except OSError:pass
  finally:c.close()
class Server(ThreadingHTTPServer):
 request_queue_size=128;daemon_threads=True
 def __init__(self,*a):self.slots=threading.BoundedSemaphore(24);super().__init__(*a)
 def process_request(self,request,address):
  if not self.slots.acquire(False):self.shutdown_request(request);return
  try:super().process_request(request,address)
  except BaseException:self.slots.release();raise
 def process_request_thread(self,*args):
  try:super().process_request_thread(*args)
  finally:self.slots.release()
server=Server(('0.0.0.0',18082),Handler);signal.signal(signal.SIGTERM,lambda *_:threading.Thread(target=server.shutdown,daemon=True).start())
try:server.serve_forever()
finally:server.server_close()
