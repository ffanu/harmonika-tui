import hashlib,importlib.util,json,pathlib,secrets,sqlite3,tempfile,unittest,socket,time
from unittest import mock
BASE=pathlib.Path(__file__).resolve().parents[1]
def load(name,path):
 s=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m
S=load('level12_security',BASE/'scripts/level12-lab/security.py')
class SecurityTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();p=pathlib.Path(self.tmp.name);self.password=secrets.token_urlsafe(24);salt=secrets.token_bytes(16);cfg=p/'fixture.json';cfg.write_text(json.dumps({'users':{'admin':{'role':'admin','salt':salt.hex(),'hash':hashlib.pbkdf2_hmac('sha256',self.password.encode(),salt,180000).hex()}}}));self.origin='https://lab.asrafijm.com';self.s=S.Security(p/'db.sqlite',cfg,self.origin);self.headers={'Origin':self.origin};self.path=p
 def tearDown(self):self.tmp.cleanup()
 def err(self,status,fn):
  with self.assertRaises(S.AuthError) as cm:fn()
  self.assertEqual(cm.exception.status,status)
 def login(self):
  v,c=self.s.login({'username':'admin','password':self.password},self.headers);return v,{'Origin':self.origin,'Cookie':c.split(';')[0],'X-CSRF-Token':v['csrf']}
 def test_secure_cookie_and_hashed_token(self):
  v,h=self.login();_,cookie=self.s.login({'username':'admin','password':self.password},self.headers)
  for flag in ['Secure','HttpOnly','SameSite=Strict']:self.assertIn(flag,cookie)
  with sqlite3.connect(self.path/'db.sqlite') as c:rows=c.execute('SELECT token FROM sessions').fetchall()
  self.assertTrue(all(len(row[0])==64 for row in rows));self.assertNotIn(h['Cookie'].split('=')[1],str(rows))
 def test_csrf_origin_and_forged(self):
  v,h=self.login();self.s.require_mutation(h,v['csrf']);self.err(403,lambda:self.s.require_mutation({**h,'Origin':'https://evil.example'},v['csrf']));self.err(403,lambda:self.s.require_mutation({**h,'X-CSRF-Token':'wrong'},v['csrf']));self.err(403,lambda:self.s.resolve({**h,'X-Role':'admin'}));self.err(401,lambda:self.s.resolve({}))
 def test_login_origin_and_throttle(self):
  self.err(403,lambda:self.s.login({'username':'admin','password':self.password},{}));self.err(401,lambda:self.s.login({'username':'admin','password':'wrong'},self.headers));self.err(429,lambda:self.s.login({'username':'admin','password':'wrong'},self.headers))
 def test_session_persists_and_logout_revokes(self):
  v,h=self.login();second=S.Security(self.path/'db.sqlite',self.path/'fixture.json',self.origin);self.assertEqual(second.resolve(h)[0]['username'],'admin');second.logout(h);self.err(401,lambda:self.s.resolve(h))
 def test_cookie_name_prefix_does_not_bypass_logout(self):
  v,h=self.login();h['Cookie']='not_hl12_session=aaaaaaaaaaaaaaaaaaaaaaaaaaa; '+h['Cookie'];self.s.logout(h);self.err(401,lambda:self.s.resolve(h))
 def test_read_session_survives_writer(self):
  v,h=self.login();c=sqlite3.connect(self.path/'db.sqlite');c.execute('BEGIN IMMEDIATE')
  try:self.assertEqual(self.s.resolve(h)[0]['username'],'admin')
  finally:c.rollback();c.close()
class ControlGuards(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();base=pathlib.Path(self.tmp.name);self.base=base;(base/'data').mkdir();(base/'releases').mkdir();(base/'receipts').mkdir();self.db=base/'data/crm.sqlite'
  meta={'data_dir':str(base/'data'),'app_unit':'owned-app.service','edge_unit':'owned-edge.service','domain':'test.asrafijm.com','preferred_port':17881,'edge_admin_port':20219,'service_user':'owned'};(base/'meta.json').write_text(json.dumps(meta));(base/'control.py').write_bytes((BASE/'scripts/level12-lab/remote_control.py').read_bytes());self.m=load('exam_remote_test',base/'control.py')
  with sqlite3.connect(self.db) as c:
   for name in ['customers','tasks','articles']:c.execute('CREATE TABLE '+name+'(id TEXT PRIMARY KEY, value TEXT)')
   c.execute("INSERT INTO customers VALUES('one','synthetic')");c.execute('PRAGMA user_version=1');c.execute('PRAGMA journal_mode=WAL')
 def tearDown(self):self.tmp.cleanup()
 def test_pending_receipt_cannot_report_success(self):
  d=self.base/'releases/v1';d.mkdir();(d/'manifest.json').write_text(json.dumps({'release':'v1','source_sha256':'abc','hashes':{}}));(self.base/'receipts/op.json').write_text(json.dumps({'release':'v1','source_sha256':'abc','state':'intent'}))
  with self.assertRaisesRegex(RuntimeError,'operation_incomplete'):self.m.deploy('v1','op')
 def test_unlisted_release_file_rejected_before_service_control(self):
  d=self.base/'releases/v1';d.mkdir();(d/'manifest.json').write_text(json.dumps({'release':'v1','source_sha256':'abc','hashes':{}}));(d/'stray.py').write_text('')
  with self.assertRaisesRegex(RuntimeError,'release_file_set_invalid'):self.m.deploy('v1','op')
  self.assertFalse((self.base/'receipts/op.json').exists())
 def test_backup_restore_clone_and_live_advance(self):
  b=self.m.backup('one')
  with sqlite3.connect(self.db) as c:c.execute("INSERT INTO customers VALUES('two','newer')")
  out=self.m.restore('one');self.assertTrue(out['snapshot_data_match']);self.assertTrue(out['live_database_unchanged']);self.assertTrue(out['matches_backup']);self.assertEqual(out['counts']['customers'],1);self.assertEqual(out['live_after']['counts']['customers'],2)
  self.assertEqual((self.base/'backups/one/crm.sqlite').stat().st_mode&0o777,0o600)
  with self.assertRaisesRegex(RuntimeError,'backup_exists'):self.m.backup('one')
 def test_failed_backup_does_not_publish_or_burn_label(self):
  original=self.m.invariants;self.m.invariants=lambda p:(_ for _ in ()).throw(RuntimeError('injected_snapshot_failure'))
  with self.assertRaisesRegex(RuntimeError,'injected_snapshot_failure'):self.m.backup('recoverable')
  self.assertFalse((self.base/'backups/recoverable').exists());self.assertFalse((self.base/'backups/.tmp-recoverable').exists());self.m.invariants=original;self.assertTrue(self.m.backup('recoverable')['closed_snapshot'])
 def test_owned_port_holder_can_bind_after_application_time_wait(self):
  listener=socket.socket();listener.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1);listener.bind(('127.0.0.1',0));port=listener.getsockname()[1];listener.listen(1)
  client=socket.create_connection(('127.0.0.1',port),timeout=2);peer,_=listener.accept();peer.shutdown(socket.SHUT_WR);client.recv(1);client.close();peer.close();listener.close();time.sleep(.05)
  self.m.M['preferred_port']=port;(self.base/'meta.json').write_text(json.dumps(self.m.M));children=[];real_popen=self.m.subprocess.Popen
  def start(*args,**kw):
   p=real_popen(*args,**kw);children.append(p);return p
  try:
   with mock.patch.object(self.m,'status',lambda:{'app':{'MainPID':'0'}}),mock.patch.object(self.m,'run',lambda *a,**k:''),mock.patch.object(self.m,'restart_owned',lambda:{'owned_fixture_only':True}),mock.patch.object(self.m.subprocess,'Popen',start):
    result=self.m.fault('port','start');self.assertTrue(result['active'])
    probe=socket.create_connection(('127.0.0.1',port),timeout=2);probe.close()
  finally:
   (self.base/'port-release').touch()
   for child in children:child.wait(timeout=4)
if __name__=='__main__':unittest.main()
