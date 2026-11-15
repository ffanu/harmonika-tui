"""Immutable external effect process. Separate DB, atomic idempotency and fences."""
import base64,hashlib,http.server,json,os,socket,socketserver,sqlite3,stat
from pathlib import Path
ROOT=Path('/data');SOCKET=ROOT/'effect.sock'
def db():
 c=sqlite3.connect(ROOT/'sink.sqlite',timeout=2);c.row_factory=sqlite3.Row;return c
def initialize(root='/data'):
 global ROOT,SOCKET
 ROOT=Path(root);ROOT.mkdir(parents=True,exist_ok=True);SOCKET=ROOT/'effect.sock';(ROOT/'faults').mkdir(exist_ok=True)
 with db() as c:
  c.execute('PRAGMA journal_mode=WAL');c.executescript('''CREATE TABLE IF NOT EXISTS fences(job_id TEXT PRIMARY KEY,owner TEXT NOT NULL,fence INTEGER NOT NULL); CREATE TABLE IF NOT EXISTS effects(job_id TEXT PRIMARY KEY,owner TEXT NOT NULL,fence INTEGER NOT NULL,sha256 TEXT NOT NULL,content BLOB NOT NULL); CREATE TABLE IF NOT EXISTS calls(id INTEGER PRIMARY KEY,job_id TEXT,worker_id TEXT,fence INTEGER,kind TEXT); CREATE TABLE IF NOT EXISTS spent(kind TEXT PRIMARY KEY,n INTEGER NOT NULL DEFAULT 0); CREATE TABLE IF NOT EXISTS targets(kind TEXT PRIMARY KEY,job_id TEXT NOT NULL);''')
def receipt(row):
 if row is None:return None
 return {k:row[k] for k in ['job_id','owner','fence','sha256']}|{'content_b64':base64.b64encode(row['content']).decode()}
def operate(op,r):
 id=r.get('job_id');worker=r.get('worker_id');fence=r.get('fence',0);fault=None
 if op=='health':return 200,{'ready':True},None
 if not isinstance(id,str) or len(id)>80 or worker not in ['api','worker1','worker2','worker3','local','examiner']:return 422,{'error':'invalid_request'},None
 with db() as c:
  c.execute('BEGIN IMMEDIATE')
  c.execute('INSERT INTO calls(job_id,worker_id,fence,kind) VALUES(?,?,?,?)',(id,worker,fence,op))
  if op=='lookup':
   n=c.execute("SELECT n FROM spent WHERE kind='lookup-fail-two'").fetchone()
   target=c.execute("SELECT job_id FROM targets WHERE kind='lookup-fail-two'").fetchone()
   if (ROOT/'faults/lookup-fail-two').exists() and target is not None and target[0]==id and (n is None or n[0]<2):
    c.execute("INSERT INTO spent(kind,n) VALUES('lookup-fail-two',1) ON CONFLICT(kind) DO UPDATE SET n=n+1");c.execute('INSERT INTO calls(job_id,worker_id,fence,kind) VALUES(?,?,?,?)',(id,worker,0,'lookup_unavailable'));return 503,{'error':'sink_unavailable'},None
   return 200,receipt(c.execute('SELECT * FROM effects WHERE job_id=?',(id,)).fetchone()),None
  if type(fence) is not int or fence<1:return 422,{'error':'invalid_fence'},None
  previous=c.execute('SELECT * FROM fences WHERE job_id=?',(id,)).fetchone()
  if op=='authorize':
   owner=r.get('owner')
   if owner not in ['alpha','beta']:return 422,{'error':'invalid_owner'},None
   if previous is not None and (previous['owner']!=owner or previous['fence']>fence):return 409,{'error':'stale_fence'},None
   c.execute('INSERT INTO fences VALUES(?,?,?) ON CONFLICT(job_id) DO UPDATE SET fence=excluded.fence',(id,owner,fence));return 200,{'authorized':True,'fence':fence},None
  if op!='execute':return 404,{'error':'not_found'},None
  if previous is None or previous['fence']!=fence:
   c.execute('INSERT INTO calls(job_id,worker_id,fence,kind) VALUES(?,?,?,?)',(id,worker,fence,'stale_rejected'));return 409,{'error':'stale_fence'},None
  payload=r.get('payload');raw=json.dumps(payload,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode();digest=hashlib.sha256(raw).hexdigest()
  row=c.execute('SELECT * FROM effects WHERE job_id=?',(id,)).fetchone()
  if row is not None and row['sha256']!=digest:return 409,{'error':'idempotency_conflict'},None
  if row is None:c.execute('INSERT INTO effects VALUES(?,?,?,?,?)',(id,previous['owner'],fence,digest,raw))
  row=c.execute('SELECT * FROM effects WHERE job_id=?',(id,)).fetchone();value=receipt(row)
  for kind in ['unknown-ack','crash-after-effect','bad-receipt']:
   if (ROOT/('faults/'+kind)).exists() and not c.execute('SELECT 1 FROM spent WHERE kind=?',(kind,)).fetchone():
    c.execute('INSERT INTO spent(kind,n) VALUES(?,1)',(kind,));c.execute('INSERT INTO calls(job_id,worker_id,fence,kind) VALUES(?,?,?,?)',(id,worker,fence,kind));fault=kind
    if kind=='unknown-ack' and (ROOT/'faults/lookup-fail-two').exists():c.execute("INSERT OR IGNORE INTO targets VALUES('lookup-fail-two',?)",(id,))
    break
 # Commit completes before physically dropping ACK / requesting worker self-exit.
 if fault=='bad-receipt':value={**value,'content_b64':base64.b64encode(b'corrupt fixture').decode(),'sha256':'0'*64}
 return 200,value,fault
class Handler(http.server.BaseHTTPRequestHandler):
 def log_message(self,*a):pass
 def do_POST(self):
  try:
   size=int(self.headers.get('Content-Length','0'))
   if size<0 or size>8192:raise ValueError()
   r=json.loads(self.rfile.read(size));status,value,fault=operate(self.path[1:],r)
  except Exception:status,value,fault=503,{'error':'sink_unavailable'},None
  if fault=='unknown-ack':
   self.close_connection=True
   try:self.connection.shutdown(socket.SHUT_RDWR)
   except OSError:pass
   self.connection.close();return
  raw=json.dumps(value,separators=(',',':')).encode();self.send_response(status);self.send_header('Content-Length',str(len(raw)))
  if fault=='crash-after-effect':self.send_header('X-Lab-Crash-Worker','42')
  self.end_headers();self.wfile.write(raw)
class Server(socketserver.ThreadingMixIn,socketserver.UnixStreamServer):daemon_threads=True
if __name__=='__main__':
 initialize()
 if SOCKET.exists():
  if not stat.S_ISSOCK(SOCKET.lstat().st_mode) or SOCKET.lstat().st_uid!=os.getuid():raise RuntimeError('invalid_socket')
  SOCKET.unlink()
 server=Server(str(SOCKET),Handler);os.chmod(SOCKET,0o600);server.serve_forever()
