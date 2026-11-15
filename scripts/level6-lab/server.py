"""Immutable HTTP/SSE transport, lab identity fixture, and native Queue adapter."""
import json,os,signal,threading
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from urllib.parse import urlparse
from engine import Queue
from effect_connector import EffectConnector
q=Queue('/data/jobs.sqlite',EffectConnector('/data'));q.start()
class Handler(BaseHTTPRequestHandler):
 def log_message(self,*args):pass
 def respond(self,status,body,kind='application/json'):
  data=body if isinstance(body,bytes) else json.dumps(body,separators=(',',':'),ensure_ascii=False).encode()
  self.send_response(status);self.send_header('Content-Type',kind);self.send_header('Content-Length',str(len(data)));self.send_header('Cache-Control','no-store')
  if status in [429,503]:self.send_header('Retry-After','1')
  self.end_headers();self.wfile.write(data)
 def do_GET(self):self.route('GET')
 def do_POST(self):self.route('POST')
 def route(self,method):
  path=urlparse(self.path).path
  if path=='/healthz':return self.respond(200,{'status':'ok','uid':os.getuid()})
  if path=='/readyz':
   try:q.events('alpha',0);return self.respond(200,{'ready':True})
   except Exception:return self.respond(503,{'error':'not_ready'})
  owner=self.headers.get('X-Lab-Owner')
  if owner not in ['alpha','beta']:return self.respond(401,{'error':'unauthorized'})
  try:
   if method=='POST':
    size=int(self.headers.get('Content-Length','0'))
    if size<0 or size>4096:return self.respond(413,{'error':'payload_too_large'})
    raw=self.rfile.read(size)
    try:body=json.loads(raw) if raw else {}
    except ValueError:return self.respond(422,{'error':'invalid_json'})
    if path=='/jobs':return self.respond(*q.submit(owner,self.headers.get('Idempotency-Key'),body))
    if path.startswith('/jobs/') and path.endswith('/cancel'):return self.respond(*q.cancel(owner,path.split('/')[2]))
    if path=='/control/drain':return self.respond(*q.drain())
    if path=='/control/resume':return self.respond(*q.resume())
   if method=='GET':
    if path.startswith('/jobs/'):return self.respond(*q.get(owner,path.split('/')[2]))
    if path.startswith('/artifacts/'):return self.respond(*q.artifact(owner,path.split('/')[2]),kind='application/octet-stream')
    if path=='/events':
     try:cursor=int(self.headers.get('Last-Event-ID','0'))
     except ValueError:return self.respond(422,{'error':'invalid_cursor'})
     status,value=q.events(owner,cursor)
     if status!=200:return self.respond(status,value)
     wire=''.join('id: '+str(e['id'])+'\nevent: job\ndata: '+json.dumps(e,separators=(',',':'))+'\n\n' for e in value['events'])
     return self.respond(200,wire.encode(),'text/event-stream')
   return self.respond(404,{'error':'not_found'})
  except Exception:return self.respond(500,{'error':'internal_error'})
server=ThreadingHTTPServer(('127.0.0.1',18081),Handler)
def shutdown(*_):threading.Thread(target=server.shutdown,daemon=True).start()
signal.signal(signal.SIGTERM,shutdown)
try:server.serve_forever()
finally:server.server_close();q.stop()
