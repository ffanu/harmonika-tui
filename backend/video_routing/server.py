"""Loopback-only signed owner video adapter; no request logging."""
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import json
from facade import dispatch
class Handler(BaseHTTPRequestHandler):
 def log_message(self,*args):pass
 def do_POST(self):
  if self.path!='/rpc':self.send_error(404);return
  try:
   n=int(self.headers.get('Content-Length','0'))
   if not 0<n<=16384:raise ValueError()
   data=json.loads(self.rfile.read(n));out=dispatch(data)
  except Exception:out={'ok':False,'error':{'code':'invalid_request'}}
  body=json.dumps(out,separators=(',',':')).encode();self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
if __name__=='__main__':ThreadingHTTPServer(('127.0.0.1',18246),Handler).serve_forever()
