import hashlib,importlib.util,io,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
SPEC=importlib.util.spec_from_file_location('flux_install',Path(__file__).resolve().parents[2]/'backend/rtx_flux/install_windows.py');m=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(m)
class Response(io.BytesIO):
 status=206
 def __init__(self,data,start,total):super().__init__(data);self.headers={'Content-Range':f'bytes {start}-{total-1}/{total}'}
class InstallerTests(unittest.TestCase):
 def test_resume_verified_and_atomic(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);out=root/'weights.safetensors';parts=root/'weights.safetensors.parts';parts.mkdir();(parts/'0').write_bytes(b'abc')
   data=b'abcdefghij';requests=[]
   def request(req,**kw):
    requests.append(req);self.assertEqual(req.headers['Range'],'bytes=3-9');self.assertIn('hms_offset=3',req.full_url);return Response(data[3:],3,10)
   with patch.object(m,'ROOT',root),patch.object(m,'status'),patch.object(m.urllib.request,'urlopen',side_effect=request):
    m.download('https://example.invalid/weights',out,len(data),hashlib.sha256(data).hexdigest())
   self.assertEqual(out.read_bytes(),data);self.assertFalse(parts.exists());self.assertEqual(len(requests),1)
 def test_checksum_mismatch_not_published(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);out=root/'weights.safetensors'
   with patch.object(m,'ROOT',root),patch.object(m,'status'),patch.object(m.urllib.request,'urlopen',return_value=Response(b'bad',0,3)):
    with self.assertRaisesRegex(RuntimeError,'checksum'):m.download('https://example.invalid/weights',out,3,'0'*64)
   self.assertFalse(out.exists())
 def test_existing_verified_model_never_downloaded(self):
  with tempfile.TemporaryDirectory() as d:
   out=Path(d)/'model';out.write_bytes(b'existing')
   with patch.object(m.urllib.request,'urlopen',side_effect=AssertionError('no network')):
    m.download('https://example.invalid/weights',out,8,hashlib.sha256(b'existing').hexdigest())
 def test_wrong_range_is_rejected(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);out=root/'model'
   def wrong(*a,**kw):return Response(b'xxx',5,8)
   with patch.object(m,'ROOT',root),patch.object(m,'status'),patch.object(m.time,'sleep'),patch.object(m.urllib.request,'urlopen',side_effect=wrong):
    with self.assertRaisesRegex(ValueError,'range_not_supported'):m.download('https://example.invalid/w',out,3,'0'*64)
   self.assertFalse(out.exists())
if __name__=='__main__':unittest.main()
