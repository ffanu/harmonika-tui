"""Owned loopback proxy, graceful stdin shutdown, headers only in transport RAM."""
import base64,json,sys,threading
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from ops import call
class Handler(BaseHTTPRequestHandler):
 def log_message(self,*a):pass
 def do_GET(self):self.forward_request()
 def do_POST(self):self.forward_request()
 def do_PATCH(self):self.forward_request()
 def forward_request(self):
  n=int(self.headers.get('Content-Length','0'));assert n<=16384;r={'op':'http','channel':'browser','method':self.command,'path':self.path,'headers':{k:v for k,v in self.headers.items() if k.lower() in ['content-type','cookie','last-event-id','idempotency-key']}}
  if n:r['raw_b64']=base64.b64encode(self.rfile.read(n)).decode()
  try:v=call(r);status=v['http_status'] or 503;raw=base64.b64decode(v.get('body_b64',''));headers=v.get('headers',{})
  except Exception:status=503;raw=b'{"error":"bridge_unavailable"}';headers={}
  self.send_response(status)
  for k,value in headers.items():self.send_header(k,value)
  self.send_header('Content-Length',str(len(raw)));self.send_header('Cache-Control','no-store');self.end_headers()
  try:self.wfile.write(raw)
  except (BrokenPipeError,ConnectionResetError):pass
server=ThreadingHTTPServer(('127.0.0.1',0),Handler);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start();print(json.dumps({'url':'http://127.0.0.1:'+str(server.server_port)}),flush=True)
try:
 for line in sys.stdin:
  if line.strip()=='shutdown':break
finally:server.shutdown();thread.join();server.server_close()
