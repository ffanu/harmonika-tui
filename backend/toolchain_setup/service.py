"""Private backend toolchain readiness. Fixed self-tests only; no arbitrary execution."""
import hashlib,hmac,json,os,subprocess,threading,time
from pathlib import Path
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
BASE=Path('/opt/harmonika-ai/toolchain-runtime');SOURCE=Path(__file__).resolve().parent;TOKEN=Path('/etc/harmonika-ai/toolchain.token');LOCK=threading.Lock()
def status():
 report=json.loads((BASE/'verification.json').read_text());report['shared_backend']=True;report['installed_package_count']=len((BASE/'requirements.lock').read_text().splitlines());report['public_execution_enabled']=False;report['profile_coverage']=json.loads((SOURCE/'coverage.json').read_text());return report
class Handler(BaseHTTPRequestHandler):
 timeout=10
 def log_message(self,*args):pass
 def reply(self,code,out):
  b=json.dumps(out,separators=(',',':')).encode();self.send_response(code);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(b)));self.send_header('Cache-Control','no-store');self.end_headers();self.wfile.write(b)
 def do_POST(self):
  if not hmac.compare_digest(self.headers.get('X-Harmonika-Tools-Secret',''),TOKEN.read_text().strip()):return self.reply(403,{'ok':False,'error':{'code':'forbidden'}})
  try:
   n=int(self.headers.get('Content-Length','0'))
   if not 0<n<=256:raise ValueError()
   body=json.loads(self.rfile.read(n))
   if body!={}:raise ValueError()
   if self.path=='/capabilities':return self.reply(200,status())
   if self.path!='/self-test':return self.reply(404,{'ok':False,'error':{'code':'not_found'}})
   if not LOCK.acquire(False):return self.reply(429,{'ok':False,'error':{'code':'test_busy'}})
   try:
    p=subprocess.run([str(BASE/'venv/bin/python'),str(SOURCE/'verify.py'),'--output',str(BASE/'verification.json')],capture_output=True,timeout=40,env={**os.environ,'CUDA_VISIBLE_DEVICES':'','OMP_NUM_THREADS':'2','DO_NOT_TRACK':'1'})
    return self.reply(200 if p.returncode==0 else 503,status())
   finally:LOCK.release()
  except Exception:return self.reply(400,{'ok':False,'error':{'code':'toolchain_unavailable'}})
class Server(ThreadingHTTPServer):
 daemon_threads=True
 def handle_error(self,*args):pass
if __name__=='__main__':Server(('127.0.0.1',18319),Handler).serve_forever()
