"""Immutable API transport. Continuous chunked SSE polls new committed events."""
import json,os,signal,socket,threading,time
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from urllib.parse import urlparse
from engine import Queue
from effect_connector import EffectConnector
connector=EffectConnector('/data','api');q=Queue('/data/jobs.sqlite',connector,worker_id='api',lease_seconds=3.0)
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
  if path=='/readyz':
   try:q.events('alpha',0);connector.health();return self.respond(200,{'ready':True})
   except Exception:return self.respond(503,{'error':'not_ready'})
  owner=self.headers.get('X-Lab-Owner')
  if owner not in ['alpha','beta']:return self.respond(401,{'error':'unauthorized'})
  try:
   if method=='POST':
    size=int(self.headers.get('Content-Length','0'))
    if size<0 or size>4096:return self.respond(413,{'error':'payload_too_large'})
    try:body=json.loads(self.rfile.read(size)) if size else {}
    except ValueError:return self.respond(422,{'error':'invalid_json'})
    if path=='/jobs':return self.respond(*q.submit(owner,self.headers.get('Idempotency-Key'),body))
    if path.startswith('/jobs/') and path.endswith('/cancel'):return self.respond(*q.cancel(owner,path.split('/')[2]))
    if path=='/control/drain':return self.respond(*q.drain())
    if path=='/control/resume':return self.respond(*q.resume())
   if method=='GET':
    if path.startswith('/jobs/'):return self.respond(*q.get(owner,path.split('/')[2]))
    if path.startswith('/requests/'):return self.respond(*q.lookup_request(owner,path.split('/')[2]))
    if path.startswith('/artifacts/'):return self.respond(*q.artifact(owner,path.split('/')[2]),kind='application/octet-stream')
    if path in ['/events','/events/live']:
     try:cursor=int(self.headers.get('Last-Event-ID','0'))
     except ValueError:return self.respond(422,{'error':'invalid_cursor'})
     if path.endswith('/live'):return self.live(owner,cursor)
     return self.respond(*q.events(owner,cursor))
   return self.respond(404,{'error':'not_found'})
  except (BrokenPipeError,ConnectionResetError):pass
  except Exception:return self.respond(500,{'error':'internal_error'})
class Server(ThreadingHTTPServer):request_queue_size=256
server=Server(('127.0.0.1',18081),Handler)
signal.signal(signal.SIGTERM,lambda *_:threading.Thread(target=server.shutdown,daemon=True).start())
try:server.serve_forever()
finally:server.server_close();q.stop()
