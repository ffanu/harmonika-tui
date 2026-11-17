import importlib.util,json,hashlib,base64,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import sys
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'runtime/tools'))
import operations_cli as cli
class ClientTests(unittest.TestCase):
 def test_download_verifies_and_saves_without_printing_binary(self):
  raw=b'fixture trace';sha=hashlib.sha256(raw).hexdigest()
  def fetch(*a,**kw):return {'ok':True,'result':{'bytes':len(raw),'offset':0,'next_offset':len(raw),'chunk_sha256':sha,'sha256':sha,'complete':True,'data_base64':base64.b64encode(raw).decode()}}
  with tempfile.TemporaryDirectory() as tmp:
   target=Path(tmp).resolve()/'trace.zip';value=cli.download('fixture','browser_'+('a'*32)+'/trace.zip',target,fetch)
   self.assertEqual(target.read_bytes(),raw);self.assertNotIn('data_base64',value);self.assertTrue(value['ok'])
   with self.assertRaises(FileExistsError):cli.download('fixture','x',target,fetch)
   self.assertEqual(target.read_bytes(),raw)
 def test_invalid_checksum_removes_only_new_partial_file(self):
  def fetch(*a,**kw):return {'ok':True,'result':{'bytes':1,'offset':0,'next_offset':1,'chunk_sha256':'a'*64,'sha256':'a'*64,'complete':True,'data_base64':'eA=='}}
  with tempfile.TemporaryDirectory() as tmp:
   target=Path(tmp).resolve()/'trace.zip'
   with self.assertRaises(ValueError):cli.download('fixture','x',target,fetch)
   self.assertFalse(target.exists())
 def test_catalog_and_invalid_id_need_no_license_or_network(self):
  result=cli.main(['catalog']);self.assertGreater(len(result['skills']),8)
  with patch.object(cli,'request',side_effect=AssertionError('network_must_not_be_called')):
   with self.assertRaises(ValueError):cli.main(['invoke','exec.shell'])
if __name__=='__main__':unittest.main()
