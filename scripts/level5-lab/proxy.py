"""Owned loopback browser proxy; output logs contain only mechanics."""
import base64,hashlib,json,os,subprocess,sys,threading,time,socket
from pathlib import Path
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from urllib.parse import urlsplit,unquote
from recovery_proof import track
ROOT=Path(sys.argv[1]);META=json.loads((ROOT/'lab.json').read_text());REMOTE=META['remote_root']+'/controller.py'
lock=threading.Lock();state=json.loads((ROOT/'proxy-mechanics.json').read_text()) if (ROOT/'proxy-mechanics.json').exists() else {'drop_next':False,'events':[]}
state['drop_next']=False
state.setdefault('lost_ack_incidents',[])
def call(r):
 p=subprocess.run(['ssh','-o','BatchMode=yes','vps','python3 '+REMOTE],input=json.dumps(r),capture_output=True,text=True,timeout=30)
 if p.returncode:raise RuntimeError('bridge_failed')
 return json.loads(p.stdout)
def save():
 path=ROOT/'proxy-mechanics.json';tmp=path.with_suffix('.tmp');tmp.write_text(json.dumps(state));os.chmod(tmp,0o600);os.replace(tmp,path)
class H(BaseHTTPRequestHandler):
 def log_message(self,*a):pass
 def route(self):
  start=time.monotonic();path=urlsplit(self.path).path
  if path in ['/__exam/drop-ack','/__exam/disarm'] and self.command=='POST':
   with lock:state['drop_next']=path=='/__exam/drop-ack';save()
   self.send_response(200);self.end_headers();self.wfile.write(b'{"armed":true}');return
  if path.startswith('/__exam/'):
   self.send_response(404);self.end_headers();return
  try:
   size=int(self.headers.get('Content-Length','0'))
   if size>100000:raise ValueError('oversize')
   body=self.rfile.read(size) if size else None
   headers={k:v for k,v in self.headers.items() if k.lower() in ['cookie','content-type','idempotency-key','if-match']}
   origin='examiner' if self.headers.get('X-Exam-Origin')=='examiner' else 'agent'
   r=call({'op':'browser','origin':origin,'path':self.path,'method':self.command,'headers':headers,'body_b64':base64.b64encode(body).decode() if body is not None else None})
   category='/api/tickets' if path=='/api/tickets' else '/api/requests/:key' if path.startswith('/api/requests/') else '/api/other' if path.startswith('/api/') else path
   with lock:
    drop=state['drop_next'] and path=='/api/tickets' and self.command=='POST' and r['http_status']==201
    if drop:state['drop_next']=False
    request_key=self.headers.get('Idempotency-Key','') if path=='/api/tickets' and self.command=='POST' else unquote(path.split('/api/requests/',1)[1]) if path.startswith('/api/requests/') else ''
    fingerprint=hashlib.sha256(request_key.encode()).hexdigest() if request_key else None
    track(state,origin,self.command,category,r['http_status'],fingerprint,drop)
    state['events'].append({'origin':origin,'method':self.command,'route':category,'http_status':r['http_status'],'ack_dropped':drop,'latency_ms':round((time.monotonic()-start)*1000)})
    state['events']=state['events'][-1500:];save()
   if drop:self.connection.shutdown(socket.SHUT_RDWR);self.connection.close();return
   payload=base64.b64decode(r['body_b64']);self.send_response(r['http_status'])
   for k,v in r['headers'].items():self.send_header(k,v)
   self.send_header('Content-Length',str(len(payload)));self.end_headers();self.wfile.write(payload)
  except (OSError,RuntimeError,ValueError,subprocess.SubprocessError):
   try:self.send_response(502);self.end_headers();self.wfile.write(b'{"error":"bridge_unavailable"}')
   except OSError:pass
 do_GET=route;do_POST=route;do_PATCH=route;do_DELETE=route
class Server(ThreadingHTTPServer):allow_reuse_address=True;daemon_threads=True
server=Server(('127.0.0.1',int(sys.argv[2]) if len(sys.argv)>2 else 0),H);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
print(json.dumps({'url':'http://127.0.0.1:'+str(server.server_address[1])}),flush=True)
try:
 for line in sys.stdin:
  if line.strip()=='shutdown':break
finally:server.shutdown();server.server_close();thread.join()
