"""Private loopback RPC for long videos; access keys never enter responses."""
import base64,hmac,json,os,re,threading
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parent))
from engine import Engine,Failure,IDS
TOKEN=Path('/etc/harmonika-ai/video-toolkit.token').read_text().strip()
engine=Engine('/var/lib/harmonika-flex-video',Path('/etc/harmonika-ai/rtx-gateway-worker.token').read_text().strip())
class Handler(BaseHTTPRequestHandler):
 def log_message(self,*args):pass
 def do_POST(self):
  try:
   if self.path!='/rpc' or not hmac.compare_digest(self.headers.get('Authorization',''),'Bearer '+TOKEN):raise Failure('unauthorized')
   n=int(self.headers.get('Content-Length',0))
   if not 0<n<=12*1024**2:raise Failure('invalid_request')
   v=json.loads(self.rfile.read(n));owner=v.get('owner');action=v.get('action')
   if not isinstance(owner,str) or not re.fullmatch('[a-f0-9]{64}',owner):raise Failure('invalid_owner')
   if action not in {'capabilities','submit','request','job','download'}:raise Failure('invalid_action')
   if action=='capabilities':out={'ok':True,'flexible_duration':True,'minimum_duration_seconds':1,'chunk_seconds':10,'resource_admission':'available_disk','reference_conditioned':True,'quality':'preview','seamless_continuity_guaranteed':False}
   elif action=='submit':out=engine.submit(owner,v.get('request_id',''),v.get('input'))
   else:
    key=v.get('request_id') if action=='request' else v.get('job_id')
    if not isinstance(key,str) or not IDS.fullmatch(key):raise Failure('invalid_request')
    out=engine.row(owner,key,action=='request')
    if action=='download':
     if out['state']!='completed':raise Failure('video_not_ready')
     p=engine.root/key/'result.mp4';offset=v.get('offset',0)
     if type(offset) is not int or not 0<=offset<p.stat().st_size:raise Failure('invalid_offset')
     with p.open('rb') as f:f.seek(offset);chunk=f.read(512*1024)
     out={'ok':True,'provider':'Harmonika Ai','chunked':True,'mime_type':'video/mp4','offset':offset,'total_bytes':p.stat().st_size,'sha256':out['result']['sha256'],'duration_seconds':out['result']['duration_seconds'],'data':base64.b64encode(chunk).decode()}
   body=json.dumps(out).encode()
  except Exception as e:body=json.dumps({'ok':False,'error':{'code':str(e) if isinstance(e,Failure) else 'invalid_request'}}).encode()
  self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
if __name__=='__main__':
 os.umask(0o077)
 threading.Thread(target=engine.worker,daemon=True).start()
 ThreadingHTTPServer(('127.0.0.1',18258),Handler).serve_forever()
