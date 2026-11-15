import importlib.util,json,pathlib,shutil,subprocess,tempfile,unittest
from unittest.mock import patch
SRC=pathlib.Path(__file__).resolve().parents[1]/'scripts/level12-lab/ops.py'
class TypedOps(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();root=pathlib.Path(self.tmp.name);(root/'tools').mkdir();shutil.copy2(SRC,root/'tools/ops.py');(root/'tools/lab.json').write_text(json.dumps({'controller':'/private/fixture/controller.py'}));s=importlib.util.spec_from_file_location('typed_ops_test',root/'tools/ops.py');self.m=importlib.util.module_from_spec(s);s.loader.exec_module(self.m)
 def tearDown(self):self.tmp.cleanup()
 def test_successful_error_http_payload_is_normal_result(self):
  value={'status':503,'body':{'error':'sqlite_busy'}}
  with patch.object(self.m.subprocess,'run',return_value=subprocess.CompletedProcess([],0,json.dumps(value),'')):self.assertEqual(self.m.call({'op':'api'}),value)
 def test_transport_error_enum_preserved(self):
  for code in ['tls_handshake_failed','fixture_login_failed','operation_incomplete','phase_denied']:
   with patch.object(self.m.subprocess,'run',return_value=subprocess.CompletedProcess([],1,json.dumps({'error':code}),'')):
    with self.assertRaisesRegex(RuntimeError,'^'+code+'$'):self.m.call({'op':'status'})
 def test_untrusted_error_text_or_invalid_json_never_leaks(self):
  for output,expected in [('not JSON synthetic-private-detail','exam_operation_response_invalid'),(json.dumps({'error':'synthetic_private_detail'}),'exam_operation_rejected'),(json.dumps({'error':{'message':'synthetic-private-detail'}}),'exam_operation_rejected')]:
   with patch.object(self.m.subprocess,'run',return_value=subprocess.CompletedProcess([],1,output,'')):
    with self.assertRaisesRegex(RuntimeError,'^'+expected+'$'):self.m.call({'op':'api'})
 def test_history_list_is_preserved(self):
  with patch.object(self.m.subprocess,'run',return_value=subprocess.CompletedProcess([],0,'[]','')):self.assertEqual(self.m.call({'op':'history'}),[])
if __name__=='__main__':unittest.main()
