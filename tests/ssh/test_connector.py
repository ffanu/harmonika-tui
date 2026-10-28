import base64, importlib.util, json, logging, os, socket, sqlite3, stat, subprocess, tempfile, threading, time, unittest
from pathlib import Path
from unittest.mock import patch
import paramiko
SOURCE=Path(__file__).resolve().parents[2]
spec=importlib.util.spec_from_file_location('ssh_connector',SOURCE/'runtime/tools/ssh_connector.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
logging.getLogger('paramiko').addHandler(logging.NullHandler());logging.getLogger('paramiko').propagate=False
class Files(paramiko.SFTPServerInterface):
 def __init__(self,server,*args,root,**kwargs):super().__init__(server,*args,**kwargs);self.root=Path(root)
 def file(self,path):
  p=self.root/path.lstrip('/')
  if not p.resolve().is_relative_to(self.root):raise OSError('outside')
  return p
 def canonicalize(self,path):return str(Path('/'+path.lstrip('/')))
 def stat(self,path):
  try:return paramiko.SFTPAttributes.from_stat(self.file(path).stat())
  except OSError as e:return paramiko.SFTPServer.convert_errno(e.errno or 13)
 def lstat(self,path):
  try:
   p=self.root/path.lstrip('/')
   if not p.parent.resolve().is_relative_to(self.root):raise PermissionError()
   return paramiko.SFTPAttributes.from_stat(p.lstat())
  except OSError as e:return paramiko.SFTPServer.convert_errno(e.errno or 13)
 def open(self,path,flags,attr):
  try:
   fd=os.open(self.file(path),flags,0o600);f=os.fdopen(fd,'r+b' if flags&os.O_RDWR else 'wb' if flags&os.O_WRONLY else 'rb');h=paramiko.SFTPHandle(flags);h.readfile=f;h.writefile=f;return h
  except OSError as e:return paramiko.SFTPServer.convert_errno(e.errno or 13)
 def chattr(self,path,attr):
  try:paramiko.SFTPServer.set_file_attr(self.file(path),attr);return paramiko.SFTP_OK
  except OSError as e:return paramiko.SFTPServer.convert_errno(e.errno or 13)
 def remove(self,path):
  try:self.file(path).unlink();return paramiko.SFTP_OK
  except OSError as e:return paramiko.SFTPServer.convert_errno(e.errno or 13)
class Auth(paramiko.ServerInterface):
 def __init__(self,key):self.key=key
 def check_auth_publickey(self,username,key):return paramiko.AUTH_SUCCESSFUL if username=='staging' and key==self.key else paramiko.AUTH_FAILED
 def get_allowed_auths(self,username):return 'publickey'
 def check_channel_request(self,kind,chanid):return paramiko.OPEN_SUCCEEDED if kind=='session' else paramiko.OPEN_FAILED_ADMINISTRATIVELY_PROHIBITED
 def check_channel_exec_request(self,channel,command):
  def work():
   import shlex
   try:
    argv=shlex.split(command.decode());time.sleep(.02)
    if argv[0]=='/fixture/timeout':time.sleep(2);data=b'late';code=0
    elif argv[0]=='/fixture/output':data=b'x'*70000;code=0
    elif argv[0]=='/fixture/fail':data=b'failed';code=7
    elif argv[0]=='/fixture/echo':data=' '.join(argv[1:]).encode();code=0
    elif argv[0]=='/usr/bin/uname':r=subprocess.run(argv,capture_output=True,timeout=2);data=r.stdout;code=r.returncode
    else:data=b'not permitted';code=127
    channel.sendall(data);channel.send_exit_status(code);channel.close()
   except Exception:channel.close()
  threading.Thread(target=work,daemon=True).start();return True
class Stage:
 def __init__(self,root,key,host):
  self.root=root;self.key=key;self.host=host;self.stop=threading.Event();self.transports=[];self.listener=socket.socket();self.listener.bind(('127.0.0.1',0));self.listener.listen(8);self.listener.settimeout(.2);self.port=self.listener.getsockname()[1];self.thread=threading.Thread(target=self.run,daemon=True);self.thread.start()
 def run(self):
  while not self.stop.is_set():
   try:sock,_=self.listener.accept()
   except socket.timeout:continue
   except OSError:break
   t=paramiko.Transport(sock);self.transports.append(t);t.add_server_key(self.host);t.set_subsystem_handler('sftp',paramiko.SFTPServer,Files,root=self.root)
   def handle(t=t):
    try:
     t.start_server(server=Auth(self.key));channels=[]
     while t.is_active() and not self.stop.is_set():
      c=t.accept(.2)
      if c:channels.append(c)
    except Exception:pass
   threading.Thread(target=handle,daemon=True).start()
 def close(self):
  self.stop.set();self.listener.close()
  for t in self.transports:t.close()
  self.thread.join(2)
class ConnectorTests(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  cls.tmp=tempfile.TemporaryDirectory();cls.root=Path(cls.tmp.name).resolve();cls.remote=cls.root/'server';(cls.remote/'remote').mkdir(parents=True);cls.key=paramiko.RSAKey.generate(2048);cls.host=paramiko.RSAKey.generate(2048);cls.identity=cls.root/'identity';cls.key.write_private_key_file(str(cls.identity));cls.identity.chmod(0o600);cls.stage=Stage(cls.remote,cls.key,cls.host);cls.known=cls.root/'known_hosts';cls.known.write_text('[127.0.0.1]:'+str(cls.stage.port)+' '+cls.host.get_name()+' '+cls.host.get_base64()+'\n');cls.known.chmod(0o600)
 @classmethod
 def tearDownClass(cls):cls.stage.close();cls.tmp.cleanup()
 def setUp(self):
  self.area=Path(tempfile.mkdtemp(dir=self.root));self.project=self.area/'projects'/'demo';self.project.mkdir(parents=True);self.config=self.area/'targets.json';self.state=self.area/'state';self.value={'schema_version':1,'local_roots':[str(self.area/'projects')],'targets':{'stage':{'host':'127.0.0.1','port':self.stage.port,'username':'staging','identity_file':str(self.identity),'known_hosts':str(self.known),'remote_root':'/remote','allow_upload':True,'allow_download':True,'allowed_executables':['/usr/bin/uname','/fixture/echo','/fixture/fail','/fixture/timeout','/fixture/output']}}};self.save();self.env=patch.dict(os.environ,{'HARMONIKA_SSH_STATE_DIR':str(self.state)});self.env.start()
 def tearDown(self):self.env.stop()
 def save(self):self.config.write_text(json.dumps(self.value));self.config.chmod(0o600)
 def call(self,skill,args=None):return m.invoke(skill,{'target':'stage',**(args or {})},str(self.config))
 def test_real_key_connect(self):
  v=self.call('ssh.connect');self.assertTrue(v['ok']);self.assertFalse(v['data']['persistent_session'])
 def test_real_remote_exec(self):
  v=self.call('ssh.exec',{'argv':['/usr/bin/uname','-s']});self.assertTrue(v['ok']);self.assertEqual(v['data']['stdout'].strip(),os.uname().sysname)
 def test_argv_is_not_shell(self):
  text='hello; $(touch SHOULD_NOT_EXIST)';v=self.call('ssh.exec',{'argv':['/fixture/echo',text]});self.assertTrue(v['ok']);self.assertEqual(v['data']['stdout'],text)
 def test_nonzero_not_success(self):
  v=self.call('ssh.exec',{'argv':['/fixture/fail']});self.assertFalse(v['ok']);self.assertEqual(v['data']['exit_code'],7)
 def test_timeout_uncertain(self):
  v=self.call('ssh.exec',{'argv':['/fixture/timeout'],'timeout_seconds':1});self.assertEqual(v['status'],'uncertain');self.assertFalse(v['error']['retry_safe'])
 def test_output_bounded(self):self.assertEqual(self.call('ssh.exec',{'argv':['/fixture/output']})['error']['code'],'ssh_output_limit')
 def test_unknown_alias_no_network(self):
  with patch.object(m,'connection',side_effect=AssertionError('should not connect')):self.assertEqual(self.call('ssh.connect',{'target':'other'})['error']['code'],'target_not_registered')
 def test_unknown_host_refused(self):
  unknown=self.area/'unknown';unknown.write_text('');unknown.chmod(0o600);self.value['targets']['stage']['known_hosts']=str(unknown);self.save();self.assertEqual(self.call('ssh.connect')['error']['code'],'ssh_host_key_rejected')
 def test_changed_host_refused(self):
  other=paramiko.RSAKey.generate(2048);k=self.area/'bad';k.write_text('[127.0.0.1]:'+str(self.stage.port)+' '+other.get_name()+' '+other.get_base64()+'\n');k.chmod(0o600);self.value['targets']['stage']['known_hosts']=str(k);self.save();self.assertEqual(self.call('ssh.connect')['error']['code'],'ssh_host_key_rejected')
 def test_auth_failure(self):
  k=self.area/'wrong-key';paramiko.RSAKey.generate(2048).write_private_key_file(str(k));k.chmod(0o600);self.value['targets']['stage']['identity_file']=str(k);self.save();self.assertEqual(self.call('ssh.connect')['error']['code'],'ssh_auth_failed')
 def test_no_password_or_host_in_payload(self):self.assertEqual(self.call('ssh.connect',{'password':'private-value'})['error']['code'],'invalid_input')
 def test_unregistered_command(self):self.assertEqual(self.call('ssh.exec',{'argv':['/bin/sh','-c','true']})['error']['code'],'ssh_command_not_allowed')
 def test_roundtrip_exclusive(self):
  (self.project/'input.txt').write_text('Fixture data\nwith UTF-8 🌏');name=self.area.name+'.txt';args={'project_root':str(self.project),'local_path':'input.txt','remote_path':name};up=self.call('ssh.upload',args);self.assertTrue(up['ok'],up);self.assertEqual(self.call('ssh.upload',args)['error']['code'],'destination_exists');down=self.call('ssh.download',{**args,'local_path':'received.txt'});self.assertTrue(down['ok'],down);self.assertEqual((self.project/'input.txt').read_bytes(),(self.project/'received.txt').read_bytes());self.assertEqual(up['data']['sha256'],down['data']['sha256']);self.assertEqual(self.call('ssh.download',{**args,'local_path':'received.txt'})['error']['code'],'destination_exists');(self.remote/'remote'/name).unlink()
 def test_local_private_and_traversal_denied(self):
  for path in ['../outside','.env','secrets/key','key.pem','/etc/passwd']:
   v=self.call('ssh.upload',{'project_root':str(self.project),'local_path':path,'remote_path':'x'});self.assertFalse(v['ok'])
 def test_remote_symlink_denied(self):
  link=self.remote/'remote'/self.area.name;link.symlink_to('/etc');(self.project/'x').write_text('x');v=self.call('ssh.upload',{'project_root':str(self.project),'local_path':'x','remote_path':self.area.name+'/x'});self.assertEqual(v['error']['code'],'path_denied');link.unlink()
 def test_unsafe_config_denied(self):self.config.chmod(0o644);self.assertEqual(self.call('ssh.connect')['error']['code'],'unsafe_config_file')
 def test_hardlink_upload_denied(self):
  original=self.area/'original';original.write_text('private');os.link(original,self.project/'linked')
  v=self.call('ssh.upload',{'project_root':str(self.project),'local_path':'linked','remote_path':'linked'})
  self.assertEqual(v['error']['code'],'path_denied')
 def test_denied_exec_never_connects(self):
  with patch.object(m,'connection',side_effect=AssertionError('must not connect')):
   self.assertEqual(self.call('ssh.exec',{'argv':['/bin/sh']})['error']['code'],'ssh_command_not_allowed')
 def test_audit_failure_blocks_network(self):
  with patch.object(m,'audit',side_effect=OSError('disk full')),patch.object(m,'connection',side_effect=AssertionError('must not connect')):
   v=self.call('ssh.connect');self.assertFalse(v['ok']);self.assertFalse(v['audit_available'])
 def test_utf8_output(self):
  v=self.call('ssh.exec',{'argv':['/fixture/echo','Harmonika 🌏 siap']});self.assertEqual(v['data']['stdout'],'Harmonika 🌏 siap')
 def test_audit_has_mechanics_only(self):
  v=self.call('ssh.exec',{'argv':['/fixture/echo','DO_NOT_STORE_PRIVATE_PAYLOAD']});self.assertTrue(v['ok']);db=sqlite3.connect(self.state/'audit.sqlite');self.assertEqual([r[1]for r in db.execute('PRAGMA table_info(events)')],['timestamp','skill_id','status','error_type','latency_ms']);self.assertNotIn('PRIVATE_PAYLOAD',str(db.execute('SELECT * FROM events').fetchall()));self.assertEqual(db.execute('PRAGMA journal_mode').fetchone()[0],'wal');db.close()
if __name__=='__main__':unittest.main()
