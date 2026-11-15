"""Immutable API transport. Continuous chunked SSE polls new committed events."""
import json,os,signal,socket,threading,time
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from urllib.parse import urlparse
from engine import Queue
from effect_connector import EffectConnector
from workflow_engine import WorkflowStore
from workflow_adapter import ChildClient
from socketserver import ThreadingUnixStreamServer
from pathlib import Path
import sqlite3
import fault_store as faults
connector=EffectConnector('/data','api');q=Queue('/data/jobs.sqlite',connector,worker_id='api',lease_seconds=3.0)
store=WorkflowStore('/data/workflows.sqlite',ChildClient(),schema_version=int(os.environ['WORKFLOW_SCHEMA']))
class Handler(BaseHTTPRequestHandler):
 protocol_version='HTTP/1.1'
 def log_message(self,*a):pass
 def respond(self,status,body,kind='application/json'):
  data=body if isinstance(body,bytes) else json.dumps(body,separators=(',',':'),ensure_ascii=False).encode();self.send_response(status);self.send_header('Content-Type',kind);self.send_header('Content-Length',str(len(data)));self.send_header('Cache-Control','no-store')
  if status in [429,503]:self.send_header('Retry-After','1')
  self.end_headers();self.wfile.write(data)
 def do_POST(self):self.route('POST')
 def do_GET(self):self.route('GET')
 def chunk(self,raw):
  for i in range(0,len(raw),7):
   b=raw[i:i+7];self.wfile.write(('%x\r\n'%len(b)).encode()+b+b'\r\n');self.wfile.flush()
 def live(self,owner,cursor):
  status,value=q.events(owner,cursor)
  if status!=200:return self.respond(status,value)
  try:cut=int(self.headers.get('X-Lab-Disconnect-After','0'))
  except ValueError:return self.respond(422,{'error':'invalid_disconnect'})
  if cut<0 or cut>20:return self.respond(422,{'error':'invalid_disconnect'})
  self.send_response(200);self.send_header('Content-Type','text/event-stream');self.send_header('Transfer-Encoding','chunked');self.send_header('Cache-Control','no-store');self.end_headers();count=0;started=time.monotonic();last_ping=0
  try:
   self.chunk(': terhubung ✓\n\n'.encode())
   while time.monotonic()-started<10:
    status,value=q.events(owner,cursor)
    if status!=200:break
    for e in value['events']:
     raw=('id: '+str(e['id'])+'\r\nevent: job\r\ndata: '+json.dumps(e,separators=(',',':'))+'\r\n\r\n').encode()
     if cut and count>=cut:
      self.chunk(raw[:11]);self.close_connection=True;self.connection.shutdown(socket.SHUT_RDWR);self.connection.close();return
     self.chunk(raw);cursor=e['id'];count+=1
    if time.monotonic()-last_ping>.5:self.chunk(': menunggu ✓\n\n'.encode());last_ping=time.monotonic()
    time.sleep(.05)
   self.wfile.write(b'0\r\n\r\n');self.wfile.flush()
  except (BrokenPipeError,ConnectionResetError,OSError):self.close_connection=True
 def route(self,method):
  path=urlparse(self.path).path
  if path=='/healthz':return self.respond(200,{'status':'ok','uid':os.getuid()})
  if path in ['/readyz','/queue-readyz']:
   try:
    q.events('alpha',0);connector.health()
    with sqlite3.connect('/data/workflows.sqlite') as c:assert c.execute('PRAGMA user_version').fetchone()[0]>=int(os.environ['WORKFLOW_SCHEMA'])
    return self.respond(200,{'ready':True})
   except Exception:return self.respond(503,{'error':'not_ready'})
  owner=self.headers.get('X-Lab-Owner')
  if owner not in ['alpha','beta']:return self.respond(401,{'error':'unauthorized'})
  try:
   if method=='POST':
    size=int(self.headers.get('Content-Length','0'))
    if size<0 or size>8192:return self.respond(413,{'error':'payload_too_large'})
    try:body=json.loads(self.rfile.read(size)) if size else {}
    except ValueError:return self.respond(422,{'error':'invalid_json'})
    if path=='/workflows':return self.respond(*store.submit(owner,self.headers.get('Idempotency-Key'),body))
    if path.startswith('/workflows/') and path.rsplit('/',1)[-1] in ['cancel','pause','resume']:
     action=path.rsplit('/',1)[-1];fn=getattr(store,action,None)
     return self.respond(*(fn(owner,path.split('/')[2]) if fn else (404,{'error':'not_found'})))
    if path=='/jobs':
     key=self.headers.get('Idempotency-Key');status,value=q.submit(owner,key,body);id=value.get('job',{}).get('id');faults.audit('child_submit',owner,key or '',id,status)
     if status in [200,202] and self.headers.get('X-Lab-Child')=='1' and faults.take('child-submit-ack-loss',owner,key,id):
      faults.audit('child_submit_ack_lost',owner,key,id,status);self.close_connection=True;self.connection.shutdown(socket.SHUT_RDWR);self.connection.close();return
     return self.respond(status,value)
    if path.startswith('/jobs/') and path.endswith('/cancel'):return self.respond(*q.cancel(owner,path.split('/')[2]))
    if path=='/control/drain':return self.respond(*q.drain())
    if path=='/control/resume':return self.respond(*q.resume())
   if method=='GET':
    if path.startswith('/workflows/'):return self.respond(*store.get(owner,path.split('/')[2]))
    if path.startswith('/workflow-requests/'):return self.respond(*store.lookup_request(owner,path.split('/')[2]))
    if path.startswith('/workflow-artifacts/'):return self.respond(*store.artifact(owner,path.split('/')[2]),kind='application/octet-stream')
    if path=='/workflow-events':
     try:cursor=int(self.headers.get('Last-Event-ID','0'))
     except ValueError:return self.respond(422,{'error':'invalid_cursor'})
     return self.respond(*store.events(owner,cursor))
    if path.startswith('/jobs/'):return self.respond(*q.get(owner,path.split('/')[2]))
    if path.startswith('/requests/'):
     key=path.split('/')[2]
     if faults.lookup_fault(owner,key):faults.audit('child_lookup_unavailable',owner,key,status=503);return self.respond(503,{'error':'queue_unavailable'})
     status,value=q.lookup_request(owner,key);faults.audit('child_lookup',owner,key,value.get('job',{}).get('id'),status);return self.respond(status,value)
    if path.startswith('/artifacts/'):
     id=path.split('/')[2];status,value=q.artifact(owner,id)
     if status==200 and faults.take('bad-child-artifact',owner,job_id=id):faults.audit('child_artifact_corrupt',owner,job_id=id,status=200);value=b'CORRUPT_SYNTHETIC_ARTIFACT'
     else:faults.audit('child_artifact',owner,job_id=id,status=status)
     return self.respond(status,value,kind='application/octet-stream')
    if path in ['/events','/events/live']:
     try:cursor=int(self.headers.get('Last-Event-ID','0'))
     except ValueError:return self.respond(422,{'error':'invalid_cursor'})
     if path.endswith('/live'):return self.live(owner,cursor)
     return self.respond(*q.events(owner,cursor))
   return self.respond(404,{'error':'not_found'})
  except (BrokenPipeError,ConnectionResetError):pass
  except Exception:return self.respond(500,{'error':'internal_error'})
class Server(ThreadingHTTPServer):request_queue_size=256
class UnixServer(ThreadingUnixStreamServer):daemon_threads=True
socket_path=Path('/data/queue.sock');socket_path.unlink(missing_ok=True);unix=UnixServer(str(socket_path),Handler);os.chmod(socket_path,0o600)
threading.Thread(target=unix.serve_forever,daemon=True).start()
server=Server(('127.0.0.1',18081),Handler)
signal.signal(signal.SIGTERM,lambda *_:threading.Thread(target=server.shutdown,daemon=True).start())
try:server.serve_forever()
finally:server.server_close();unix.shutdown();unix.server_close();q.stop();store.stop()
