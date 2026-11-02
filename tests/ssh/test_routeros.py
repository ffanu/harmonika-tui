"""Real SSH transport to a synthetic RouterOS CLI; not a physical router test."""
import json, sqlite3, unittest
from unittest.mock import patch
import test_connector as f
m=f.m

class RouterOS(unittest.TestCase):
 @classmethod
 def setUpClass(cls):f.ConnectorTests.setUpClass()
 @classmethod
 def tearDownClass(cls):f.ConnectorTests.tearDownClass()
 def setUp(self):
  self.fixture=f.ConnectorTests();self.fixture.setUp()
  self.t=self.fixture.value['targets']['stage']
  self.t.update(platform='routeros',allow_upload=False,allow_download=False,allowed_executables=[],allowed_routeros_paths=['/system/resource','/system/identity','/ip/firewall/filter'])
  self.t.pop('remote_root');self.fixture.save()
 def tearDown(self):self.fixture.tearDown()
 def test_real_key_and_routeros_argv(self):
  v=self.fixture.call('ssh.connect');self.assertTrue(v['ok'],v);self.assertEqual(v['data']['platform'],'routeros')
  for argv in [['/system/resource','print'],['/system','resource','print'],['/system resource print'],['/system/resource/print']]:
   v=self.fixture.call('ssh.exec',{'argv':argv});self.assertTrue(v['ok'],v);self.assertIn('fixture-routeros',v['data']['stdout'])
 def test_password_file_real_transport(self):
  secret=self.fixture.area/'password';secret.write_text('fixture-password\n');secret.chmod(0o600)
  self.t.update(auth_method='password',password_file=str(secret));self.t.pop('identity_file');self.fixture.save()
  v=self.fixture.call('ssh.exec',{'argv':['/system/resource','print']});self.assertTrue(v['ok'],v)
  self.assertNotIn('fixture-password',json.dumps(v))
  with sqlite3.connect(self.fixture.state/'audit.sqlite') as db:self.assertNotIn('fixture-password',str(db.execute('SELECT * FROM events').fetchall()))
 def test_password_bad_permissions_rejected(self):
  secret=self.fixture.area/'password';secret.write_text('fixture-password');secret.chmod(0o644)
  self.t.update(auth_method='password',password_file=str(secret));self.fixture.save()
  self.assertEqual(self.fixture.call('ssh.connect')['error']['code'],'unsafe_config_file')
 def test_cli_failure_not_success_even_exit_zero(self):
  v=self.fixture.call('ssh.exec',{'argv':['/system/identity','print']});self.assertFalse(v['ok']);self.assertEqual(v['error']['code'],'remote_exit_nonzero')
 def test_mutations_secrets_and_injections_rejected_before_network(self):
  commands=['/system reboot','/system/resource print; /system reboot','/system/resource print\n/system reboot','/system/resource print follow','/system/resource print on-event=reboot','/system/resource print where name=[/system reboot]','/system/resource print $x','/system/resource print "x"','/system/resource print count-only;','/system/resource print\r','/user/ssh-keys/private print','/ppp/secret print','/system/script print','/export show-sensitive','/ip/firewall/filter remove 0','/ip/firewall/filter set 0 disabled=yes','/ip/address print','/bin/sh -c x','/system/resource print /','/system/resource print stats stats']
  with patch.object(m,'connection',side_effect=AssertionError('must not connect')):
   for command in commands:
    with self.subTest(command=command):self.assertEqual(self.fixture.call('ssh.exec',{'argv':[command]})['error']['code'],'ssh_command_not_allowed')
 def test_safe_options(self):
  self.assertEqual(m.command(self.t,['/ip/firewall/filter','print','stats','without-paging']),'/ip/firewall/filter print stats without-paging')
 def test_unknown_platform_fails_closed(self):
  self.t['platform']='router-os';self.fixture.save();self.assertEqual(self.fixture.call('ssh.connect')['error']['code'],'invalid_config')
 def test_sensitive_menu_cannot_be_enabled(self):
  self.t['allowed_routeros_paths'].append('/ppp/secret');self.fixture.save();self.assertEqual(self.fixture.call('ssh.connect')['error']['code'],'invalid_config')
 def test_no_exit_status_not_success(self):
  from unittest.mock import MagicMock
  client=MagicMock();ch=client.get_transport.return_value.open_session.return_value
  ch.recv_ready.return_value=False;ch.recv_stderr_ready.return_value=False;ch.exit_status_ready.return_value=True;ch.recv_exit_status.return_value=-1
  with self.assertRaises(m.SSHError):m.execute(client,self.t,{'argv':['/system/resource','print']})

if __name__=='__main__':unittest.main()
