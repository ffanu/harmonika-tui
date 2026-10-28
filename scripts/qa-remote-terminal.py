#!/usr/bin/env python3
"""Owner-run real Harmonika CLI QA; temporary loopback Telnet fixture, no public listener."""
import importlib.util,json,os,shlex,subprocess,time,tempfile
from pathlib import Path
SOURCE=Path(__file__).resolve().parents[1]
s=importlib.util.spec_from_file_location('ssh',SOURCE/'runtime/tools/ssh_connector.py');ssh=importlib.util.module_from_spec(s);s.loader.exec_module(ssh)
project=Path(tempfile.mkdtemp(prefix='harmonika-remote-qa-',dir=str(Path.home()/'Projects')));project.chmod(0o700)
(project/'README.md').write_text('Isolated remote access QA; no customer data.\n')
configfile=Path.home()/'.config/harmonika/ssh-targets.json'
c=ssh.load_config();t=ssh.target(c,'chr')
fixture=r'''
import socketserver,threading,sys,json
class Handler(socketserver.StreamRequestHandler):
 def handle(self):
  self.wfile.write(b'login: ');self.wfile.flush();u=self.rfile.readline().strip()
  self.wfile.write(b'Password: ');self.wfile.flush();p=self.rfile.readline().strip()
  if u!=b'fixture-user' or p!=b'fixture-secret':return
  self.wfile.write(b'# ');self.wfile.flush()
  while True:
   line=self.rfile.readline().strip()
   if not line:return
   self.wfile.write((b'QA TELNET OK\r\n' if line==b'show status' else b'not allowed\r\n')+b'# ');self.wfile.flush()
class Server(socketserver.ThreadingTCPServer):daemon_threads=True
server=Server(('127.0.0.1',0),Handler)
print(json.dumps({'port':server.server_address[1]}),flush=True)
threading.Thread(target=server.serve_forever,daemon=True).start()
# EOF or a bounded lifetime stops only this owned test fixture.
threading.Timer(180,lambda:sys.stdin.close()).start()
sys.stdin.readline();server.shutdown();server.server_close()
'''
# Own server process must exit on EOF; timer is a daemon and never holds it alive.
fixture=fixture.replace("threading.Timer(180,lambda:sys.stdin.close()).start()","timer=threading.Timer(180,lambda:__import__('os')._exit(0));timer.daemon=True;timer.start()")
records={}
with ssh.connection(t) as client:
 stdin,stdout,stderr=client.exec_command('python3 -u -c '+shlex.quote(fixture),timeout=10)
 port=json.loads(stdout.readline())['port']
 private=[]
 try:
  for name,value in [('username','fixture-user'),('password','fixture-secret')]:
   p=project/('qa-'+name);fd=os.open(p,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
   with os.fdopen(fd,'w') as file:file.write(value)
   private.append(p)
  config=ssh.load_config();config.setdefault('remote_targets',{})['qa-telnet']={'protocol':'telnet','via_ssh':'chr','host':'127.0.0.1','port':port,'prompt':'# ','username_file':str(private[0]),'password_file':str(private[1]),'allowed_commands':['show status']}
  temp=configfile.with_suffix('.qa-new');temp.write_text(json.dumps(config,indent=2));temp.chmod(0o600);os.replace(temp,configfile)
  prompt='UJI NYATA tool remote, jangan hanya menyebut rencana. 1) Panggil remote_targets query chr.banyu.live lalu ssh_connect target root@chr.banyu.live:22059 dan ssh_exec argv ["/usr/bin/uname","-s"]. 2) Panggil telnet_connect target qa-telnet lalu telnet_exec command "show status". Ini server fixture terisolasi via SSH, bukan layanan produksi. 3) Panggil vnc_connect target chr-vnc, lalu vnc_capture target chr-vnc dengan project_root '+str(project)+' dan local_path "native-vnc-qa.png". Jangan kontrol desktop, jangan memakai bash atau membaca credential, dan jangan mengarang sukses. Laporkan output/status aktual tiap protokol.'
  started=time.monotonic()
  command=[str(Path.home()/'.local/bin/harmonika'),'run','--quiet','--cwd',str(project),'--data-dir',str(project/'state-final'),'--host','unix://'+str(project/'qa-final.sock'),prompt]
  result=subprocess.run(command,capture_output=True,text=True,timeout=150)
  records={'returncode':result.returncode,'duration_seconds':round(time.monotonic()-started,2),'response':result.stdout.strip(),'stderr_present':bool(result.stderr.strip())}
  print(json.dumps(records,ensure_ascii=False))
 finally:
  config=ssh.load_config();config.get('remote_targets',{}).pop('qa-telnet',None)
  temp=configfile.with_suffix('.qa-new');temp.write_text(json.dumps(config,indent=2));temp.chmod(0o600);os.replace(temp,configfile)
  for p in private:p.unlink(missing_ok=True)
  stdin.channel.shutdown_write()
  exitcode=stdout.channel.recv_exit_status()
  print(json.dumps({'temporary_telnet_fixture_removed':True,'fixture_exit':exitcode}))
