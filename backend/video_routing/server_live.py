"""Loopback owner transport hot-loads the validated facade for every request."""
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import importlib.util,json,threading
from pathlib import Path
LOCK=threading.BoundedSemaphore(12)
class Handler(BaseHTTPRequestHandler):
 timeout=20
 def log_message(self,*args):pass
 def do_POST(self):
  if self.path!='/rpc':self.send_error(404);return
  if not LOCK.acquire(False):self.send_error(503);return
  try:
   try:
    size=int(self.headers.get('Content-Length','0'))
    if not 0<size<=12*1024*1024:raise ValueError()
    data=json.loads(self.rfile.read(size));path=Path(__file__).parent/'facade.py';spec=importlib.util.spec_from_file_location('harmonika_live_video_rpc',path);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);out=module.dispatch(data)
   except Exception:out={'ok':False,'error':{'code':'video_unavailable'}}
   raw=json.dumps(out,separators=(',',':')).encode();self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
  finally:LOCK.release()
class Server(ThreadingHTTPServer):
 daemon_threads=True
 def handle_error(self,*args):pass
if __name__=='__main__':Server(('127.0.0.1',18264),Handler).serve_forever()
