#!/usr/bin/env python3
"""Private loopback mentor relay over the pinned exam-host SSH transport."""
import hashlib,hmac,importlib.util,json,select,socket,subprocess,threading,time
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
R=Path(Path('/tmp/harmonika-level14-root.path').read_text().strip());spec=importlib.util.spec_from_file_location('op',Path(__file__).with_name('level12-operator.py'));op=importlib.util.module_from_spec(spec);spec.loader.exec_module(op)
cfg=op.python("from pathlib import Path;print(Path('/opt/harmonika-exams/hl14-native/private-config.json').read_text())");(R/'private-lab.json').write_text(json.dumps(cfg));(R/'private-lab.json').chmod(0o600)
node=r'''const fs=require('fs'),http=require('http');let body='';process.stdin.on('data',b=>body+=b);process.stdin.on('end',()=>{const req=http.request('http://127.0.0.1:17890/v1/chat/completions',{method:'POST',headers:{'Content-Type':'application/json','X-Harmonika-Client':'internal-agent','X-Harmonika-Agent-Secret':fs.readFileSync('/etc/harmonika-ai/internal-agent.token','utf8').trim()}},res=>{let raw='';res.on('data',b=>raw+=b);res.on('end',()=>{process.stdout.write(JSON.stringify({status:res.statusCode,body:raw}))})});req.on('error',()=>{process.stdout.write('{"status":503,"body":"{}"}')});req.end(body)});'''
import shlex
class Handler(BaseHTTPRequestHandler):
 def log_message(self,*a):pass
 def do_POST(self):
  if self.path!='/v1/chat/completions' or not hmac.compare_digest(self.headers.get('X-Level14-Relay',''),cfg['secret']):self.send_error(403);return
  length=int(self.headers.get('Content-Length','0'))
  if not 1<=length<=524288:self.send_error(413);return
  body=self.rfile.read(length);start=time.time()
  try:
   p=subprocess.run(['ssh','-o','BatchMode=yes','-o','StrictHostKeyChecking=yes','vps','node -e '+shlex.quote(node)],input=body,capture_output=True,timeout=280);v=json.loads(p.stdout);out=v['body'].encode();self.send_response(v['status']);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(out)));self.end_headers();self.wfile.write(out)
   with (R/'relay-mechanics.jsonl').open('a') as f:f.write(json.dumps({'status':v['status'],'latency_ms':round((time.time()-start)*1000),'request_sha256':hashlib.sha256(body).hexdigest()})+'\n')
  except Exception:
   try:self.send_error(503)
   except Exception:pass
server=ThreadingHTTPServer(('127.0.0.1',0),Handler);threading.Thread(target=server.serve_forever,daemon=True).start();transport=op.connection();port=transport.request_port_forward('127.0.0.1',18189)
def channel_loop(ch):
 local=None
 try:
  local=socket.create_connection(('127.0.0.1',server.server_port))
  while True:
   readable,_,_=select.select([ch,local],[],[],1)
   for src in readable:
    b=src.recv(32768)
    if not b:return
    (local if src is ch else ch).sendall(b)
 finally:
  ch.close()
  if local:local.close()
(R/'relay-state.json').write_text(json.dumps({'ready':True,'loopback_remote_port':port,'loopback_local_port':server.server_port}));print(json.dumps({'relay_ready':True,'remote_port':port}),flush=True)
try:
 while not (R/'relay-stop').exists():
  ch=transport.accept(1)
  if ch is not None:threading.Thread(target=channel_loop,args=(ch,),daemon=True).start()
finally:transport.cancel_port_forward('127.0.0.1',port);transport.close();server.shutdown();server.server_close()
