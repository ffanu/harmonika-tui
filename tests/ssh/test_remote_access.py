import asyncio, contextlib, importlib.util, json, os, socket, socketserver, struct, subprocess, tempfile, threading, unittest
from pathlib import Path
from unittest.mock import patch
SOURCE=Path(__file__).resolve().parents[2]
spec=importlib.util.spec_from_file_location('remote',SOURCE/'backend/remote_access/connector.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)

class Telnet(socketserver.StreamRequestHandler):
 def handle(self):
  self.wfile.write(b'login: ');self.wfile.flush();user=self.rfile.readline().strip()
  self.wfile.write(b'Password: ');self.wfile.flush();password=self.rfile.readline().strip()
  if user!=b'fixture-user' or password!=b'fixture-secret':return
  self.wfile.write(b'# ');self.wfile.flush()
  while True:
   cmd=self.rfile.readline().strip()
   if not cmd:return
   self.server.commands.append(cmd.decode());self.wfile.write(b'fixture result\r\n# ');self.wfile.flush()

class RFB(socketserver.BaseRequestHandler):
 def read(self,size):
  data=b''
  while len(data)<size:
   chunk=self.request.recv(size-len(data))
   if not chunk:raise EOFError()
   data+=chunk
  return data
 def handle(self):
  try:
   self.request.settimeout(5);self.request.sendall(b'RFB 003.008\n');self.read(12)
   self.request.sendall(b'\x01\x01');self.read(1);self.request.sendall(struct.pack('!I',0));self.read(1)
   pixel=struct.pack('!BBBBHHHBBB3x',32,24,0,1,255,255,255,16,8,0)
   self.request.sendall(struct.pack('!HH',4,3)+pixel+struct.pack('!I',7)+b'fixture')
   while True:
    kind=self.read(1)[0]
    if kind==0:self.read(19)
    elif kind==2:
     _,count=struct.unpack('!BH',self.read(3));self.read(count*4)
    elif kind==3:
     self.read(9);self.request.sendall(b'\x00\x00'+struct.pack('!H',1)+struct.pack('!HHHHi',0,0,4,3,0)+bytes([0,128,255,0])*12)
    elif kind==4:self.server.events.append(('key',self.read(7).hex()))
    elif kind==5:self.server.events.append(('pointer',self.read(5).hex()))
    elif kind==6:
     header=self.read(7);self.read(struct.unpack('!I',header[3:])[0])
    else:return
  except (OSError,EOFError):pass

class Server(socketserver.ThreadingTCPServer):allow_reuse_address=True;daemon_threads=True

class RemoteTests(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  cls.tel=Server(('127.0.0.1',0),Telnet);cls.tel.commands=[];cls.vnc=Server(('127.0.0.1',0),RFB);cls.vnc.events=[]
  for s in (cls.tel,cls.vnc):threading.Thread(target=s.serve_forever,daemon=True).start()
 @classmethod
 def tearDownClass(cls):
  for s in (cls.tel,cls.vnc):s.shutdown();s.server_close()
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name).resolve()
  self.t={'protocol':'telnet','via_ssh':'stage','host':'127.0.0.1','port':self.tel.server_address[1],'prompt':'# ','allowed_commands':['show status']}
  for k,v in [('username','fixture-user'),('password','fixture-secret')]:
   p=self.root/k;p.write_text(v);p.chmod(0o600);self.t[k+'_file']=str(p)
 def test_real_telnet_auth(self):
  r=asyncio.run(m.telnet(self.tel.server_address[1],self.t,{},False));self.assertTrue(r['authenticated'])
 def test_real_telnet_command(self):
  r=asyncio.run(m.telnet(self.tel.server_address[1],self.t,{'command':'show status'},True));self.assertTrue(r['executed']);self.assertIn('fixture result',r['output']);self.assertIsNone(r['exit_code'])
 def test_telnet_unlisted_command_rejected(self):
  with self.assertRaises(m.ssh.SSHError):asyncio.run(m.telnet(self.tel.server_address[1],self.t,{'command':'delete all'},True))
 def test_credential_permissions(self):
  Path(self.t['password_file']).chmod(0o644)
  with self.assertRaises(m.ssh.SSHError):m.credential(self.t,'password')
 def test_registered_discovery_has_no_secrets(self):
  c={'targets':{'stage':{'host':'stage.invalid','username':'admin','identity_file':'PRIVATE','port':22059}},'remote_targets':{'screen':{'protocol':'vnc','via_ssh':'stage','password_file':'SECRET'}}}
  r=m.targets(c,'admin@stage.invalid:22059');self.assertEqual(r['targets'][0]['target'],'stage');self.assertNotIn('PRIVATE',json.dumps(m.targets(c)));self.assertNotIn('SECRET',json.dumps(m.targets(c)))
 def test_endpoint_alias_resolution(self):
  c={'targets':{'stage':{'host':'stage.invalid','username':'admin','port':22059}}}
  self.assertEqual(m.ssh.resolve_alias(c,'ssh://admin@stage.invalid:22059'),'stage')
 def test_unregistered_remote_refused(self):
  with self.assertRaises(m.ssh.SSHError):m.remote_target({'remote_targets':{}},'arbitrary','vnc')
 def vnc_call(self,skill,args=None,policy=None):
  script='import importlib.util,json; s=importlib.util.spec_from_file_location("r",'+repr(str(SOURCE/'backend/remote_access/connector.py'))+');m=importlib.util.module_from_spec(s);s.loader.exec_module(m);print(json.dumps(m.vnc('+str(self.vnc.server_address[1])+','+repr(policy or {})+','+repr(args or {})+','+repr(skill)+')))'
  return subprocess.run([os.sys.executable,'-c',script],capture_output=True,text=True,timeout=15)
 def test_real_vnc_framebuffer(self):
  r=self.vnc_call('vnc.connect');self.assertEqual(r.returncode,0,r.stderr);self.assertEqual(json.loads(r.stdout)['width'],4)
 def test_real_vnc_control(self):
  r=self.vnc_call('vnc.control',{'action':'click','x':1,'y':1},{'allow_control':True});self.assertEqual(r.returncode,0,r.stderr);self.assertTrue(json.loads(r.stdout)['action_sent']);self.assertTrue(self.vnc.events)
 def test_vnc_control_requires_policy(self):
  r=self.vnc_call('vnc.control',{'action':'click','x':1,'y':1});self.assertNotEqual(r.returncode,0)
 def test_bad_input_no_network(self):
  with patch.object(m,'forwarded',side_effect=AssertionError('must not connect')),patch.object(m.ssh,'audit'),patch.object(m.ssh,'load_config',return_value={'targets':{}}):
   r=m.invoke('vnc.connect',{'target':'bad','password':'not-allowed'});self.assertEqual(r['error']['code'],'invalid_input')
 def test_worker_source_matches_backend(self):
  self.assertEqual((SOURCE/'runtime/tools/remote_connector.py').read_bytes(),(SOURCE/'backend/remote_access/connector.py').read_bytes())

if __name__=='__main__':unittest.main()
