"""Private authenticated loopback service, bounded requests and no access/body logs."""
import argparse,hmac,json,threading
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from .common import load_private,private_path,decode,fields,OpsError
from .engine import Engine
class Handler(BaseHTTPRequestHandler):
 def log_message(self,*a):pass
 def send(self,status,value):
  raw=json.dumps(value,ensure_ascii=False,allow_nan=False,separators=(',',':')).encode()
  if len(raw)>262144:status=502;raw=b'{"ok":false,"error":{"code":"output_limit"}}'
  self.send_response(status);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(raw)));self.send_header('Cache-Control','no-store');self.end_headers()
  try:self.wfile.write(raw)
  except (OSError,BrokenPipeError):pass
 def auth(self):
  if self.client_address[0] not in ('127.0.0.1','::1'):raise OpsError('access_denied',403)
  values=self.headers.get_all('X-Harmonika-Operations-Secret',[])
  if len(values)!=1 or not values[0].isascii() or not hmac.compare_digest(values[0],self.server.token):raise OpsError('access_denied',403)
  actors=self.headers.get_all('X-Harmonika-Principal',[])
  if len(actors)!=1:raise OpsError('access_denied',403)
  return actors[0]
 def do_GET(self):
  if self.path=='/healthz':return self.send(200,{'ok':True,'service':'harmonika-operations'})
  try:
   actor=self.auth()
   from .common import authorization
   authorization(self.server.engine.config,actor)
   if self.path!='/capabilities':raise OpsError('not_found',404)
   return self.send(200,self.server.engine.capabilities())
  except OpsError as e:return self.send(e.status,{'ok':False,'error':{'code':e.code}})
 def do_POST(self):
  acquired=False
  try:
   actor=self.auth()
   if self.path!='/invoke':raise OpsError('not_found',404)
   lengths=self.headers.get_all('Content-Length',[])
   if self.headers.get('Transfer-Encoding') or len(lengths)!=1 or not lengths[0].isdigit():raise OpsError('invalid_body')
   length=int(lengths[0])
   if not 0<length<=1048576:raise OpsError('input_too_large',413)
   acquired=self.server.slots.acquire(False)
   if not acquired:raise OpsError('operations_busy',429)
   body=decode(self.rfile.read(length));fields(body,{'skill_id','input'},('skill_id','input'))
   result=self.server.engine.invoke(actor,body['skill_id'],body['input']);return self.send(200,result)
  except OpsError as e:return self.send(e.status,{'ok':False,'error':{'code':e.code}})
  except Exception:return self.send(500,{'ok':False,'error':{'code':'operations_failed'}})
  finally:
   if acquired:self.server.slots.release()
class Server(ThreadingHTTPServer):
 daemon_threads=True
 def get_request(self):
  sock,address=super().get_request();sock.settimeout(10);return sock,address

def main():
 parser=argparse.ArgumentParser();parser.add_argument('--config',required=True);args=parser.parse_args()
 cfg=load_private(args.config);key=private_path(cfg['state_key_file']).read_bytes();token=private_path(cfg['token_file']).read_text().strip()
 if not 32<=len(token)<=256:raise OpsError('invalid_service_token',503)
 server=Server(('127.0.0.1',int(cfg.get('port',18230))),Handler);server.engine=Engine(cfg,key);server.token=token;server.slots=threading.BoundedSemaphore(2)
 from .retention import prune
 def maintain():
  while True:
   try:prune(cfg['state_root'])
   except Exception:pass
   threading.Event().wait(300)
 threading.Thread(target=maintain,daemon=True).start();server.serve_forever()
if __name__=='__main__':main()
