import contextlib
import errno
import importlib.util
import io
import json
from pathlib import Path
import socket
import types
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]/'runtime/tools'
spec=importlib.util.spec_from_file_location('ssh_helper',ROOT/'net_ssh_exec.py')
h=importlib.util.module_from_spec(spec);spec.loader.exec_module(h)
VALID={'host':'127.0.0.1','port':22,'username':'operator','password':'never-echo-this-secret','command':'true','timeout':2}

class Channel:
 def __init__(self,out=b'',err=b'',status=0,hang=False):
  self.out=out;self.err=err;self.status=status;self.hang=hang;self.closed=False
 def recv_ready(self):return bool(self.out)
 def recv_stderr_ready(self):return bool(self.err)
 def recv(self,n):v,self.out=self.out[:n],self.out[n:];return v
 def recv_stderr(self,n):v,self.err=self.err[:n],self.err[n:];return v
 def exit_status_ready(self):return not self.hang
 def recv_exit_status(self):return self.status
 def close(self):self.closed=True

class SSHError(Exception):pass
class AuthError(SSHError):pass
class FakeClient:
 def __init__(self,connect_error=None,exec_error=None,channel=None):
  self.connect_error=connect_error;self.exec_error=exec_error;self.channel=channel or Channel();self.closed=False;self.started=False
 def set_missing_host_key_policy(self,p):pass
 def connect(self,**kwargs):
  if self.connect_error:raise self.connect_error
 def exec_command(self,*args,**kwargs):
  self.started=True
  if self.exec_error:raise self.exec_error
  return None,types.SimpleNamespace(channel=self.channel),None
 def close(self):self.closed=True

class ContractTests(unittest.TestCase):
 def run_main(self,payload,client=None):
  client=client or FakeClient()
  fake=types.SimpleNamespace(SSHClient=lambda:client,MissingHostKeyPolicy=object,AutoAddPolicy=object,AuthenticationException=AuthError,SSHException=SSHError)
  out=io.StringIO();err=io.StringIO()
  with patch.dict(h.sys.modules,{'paramiko':fake}),patch.object(h.sys,'stdin',io.StringIO(json.dumps(payload))),contextlib.redirect_stdout(out),contextlib.redirect_stderr(err):
   code=h.main()
  self.assertEqual(err.getvalue(),'')
  self.assertNotIn(VALID['password'],out.getvalue())
  return code,json.loads(out.getvalue()),client

 def test_non_object_and_malformed_fields_return_one_json_without_network(self):
  invalid=[None,[],True,'text',{**VALID,'port':'22'},{**VALID,'port':True},{**VALID,'port':0},{**VALID,'port':65536},{**VALID,'timeout':{}},{**VALID,'timeout':121},{**VALID,'hostkey_sha256':[]},{**VALID,'command':3},{**VALID,'password':{}},{**VALID,'key_path':'a\nb'},{**VALID,'host':'bad host'},{**VALID,'unknown':'field'}]
  for value in invalid:
   with self.subTest(value=value):
    code,result,c=self.run_main(value)
    self.assertEqual(code,2);self.assertEqual(result['error'],'invalid_request')
    self.assertEqual(result['execution_state'],'not_started');self.assertFalse(c.started)

 def test_oversized_and_invalid_json_are_rejected(self):
  for value in ['not json','{'*2000,'x'*65537]:
   with self.assertRaises(ValueError):h.parse_request(io.StringIO(value))

 def test_valid_request_preserves_command_and_optional_values(self):
  r=h.parse_request(io.StringIO(json.dumps({**VALID,'command':"printf '%s' 'hello'",'hostkey_sha256':'SHA256:'+'a'*43,'key_path':'/private/key'})))
  self.assertEqual(r['command'],"printf '%s' 'hello'")

 def test_connect_timeout_does_not_claim_command_ran(self):
  code,r,c=self.run_main(VALID,FakeClient(connect_error=socket.timeout('secret error')))
  self.assertEqual(code,6);self.assertEqual(r['error'],'ssh_connect_timeout')
  self.assertEqual(r['connection_status'],'failed');self.assertEqual(r['execution_state'],'not_started')
  self.assertFalse(r['remote_process_may_still_run']);self.assertFalse(c.started);self.assertTrue(c.closed)

 def test_auth_key_dns_and_refused_errors_are_sanitized(self):
  for error,expected in [(AuthError('password secret'),'ssh_auth_failed'),(SSHError('hostkey_mismatch secret'),'ssh_hostkey_failed'),(socket.gaierror('private dns'),'ssh_dns_failed'),(ConnectionRefusedError(errno.ECONNREFUSED,'private target'),'ssh_connection_refused')]:
   _,r,c=self.run_main(VALID,FakeClient(connect_error=error))
   self.assertEqual(r['error'],expected);self.assertEqual(r['execution_state'],'not_started');self.assertFalse(c.started)
   self.assertNotIn('private',json.dumps(r));self.assertNotIn('secret',json.dumps(r))

 def test_timeout_starting_command_is_unknown_and_must_not_retry(self):
  _,r,_=self.run_main(VALID,FakeClient(exec_error=socket.timeout('private')))
  self.assertEqual(r['execution_state'],'unknown');self.assertTrue(r['remote_process_may_still_run']);self.assertFalse(r['safe_to_retry'])

 def test_remote_nonzero_and_255_are_completed_commands_not_transport_failure(self):
  for status in [0,7,255]:
   _,r,c=self.run_main(VALID,FakeClient(channel=Channel(b'output',b'diagnostic',status)))
   self.assertEqual(r['exit_status'],status);self.assertEqual(r['execution_state'],'completed');self.assertEqual(r['connection_status'],'connected')
   self.assertEqual(r['ok'],status==0);self.assertTrue(c.closed)

 def test_both_streams_are_bounded_and_truncation_explicit(self):
  c=Channel(b'a'*60000,b'b'*24000)
  r=h.drain_channel(c,2)
  self.assertEqual(len(r['output']),20000);self.assertEqual(len(r['stderr']),4000)
  self.assertTrue(r['output_truncated']);self.assertTrue(r['stderr_truncated'])

 def test_command_timeout_preserves_partial_output_without_retry(self):
  c=Channel(b'partial',b'warning',hang=True)
  from itertools import count
  with patch.object(h.time,'monotonic',side_effect=count(0,.2)),patch.object(h.time,'sleep'):
   r=h.drain_channel(c,1)
  self.assertEqual(r['error'],'ssh_command_timeout');self.assertEqual(r['output'],'partial');self.assertEqual(r['stderr'],'warning')
  self.assertTrue(c.closed);self.assertTrue(r['remote_process_may_still_run']);self.assertFalse(r['safe_to_retry'])

 def test_missing_exit_is_unknown_not_success(self):
  r=h.drain_channel(Channel(status=-1),2)
  self.assertFalse(r['ok']);self.assertEqual(r['execution_state'],'unknown');self.assertEqual(r['error'],'ssh_exit_status_unknown')

if __name__=='__main__':unittest.main()
