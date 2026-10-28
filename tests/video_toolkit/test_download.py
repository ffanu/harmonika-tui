import hashlib,importlib.util,io,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
BASE=Path(__file__).resolve().parents[2]/'backend/video_toolkit'
s=importlib.util.spec_from_file_location('video_download',BASE/'download.py');d=importlib.util.module_from_spec(s);s.loader.exec_module(d)
class Response(io.BytesIO):
 status=206
 def __init__(self,raw,start):super().__init__(raw);self.headers={'Content-Range':f'bytes {start}-{start+len(raw)-1}/{start+len(raw)}'}
 def read1(self,n=-1):return super().read(min(n,2))
class Downloads(unittest.TestCase):
 def test_short_stream_chunks_resume_and_verify(self):
  raw=b'abcdefghijklm'
  with tempfile.TemporaryDirectory() as folder,patch.object(d,'ROOT',Path(folder)),patch.object(d,'STATUS',Path(folder)/'status.json'):
   p=Path(folder)/'model.safetensors';parts=p.parent/(p.name+'.parts');parts.mkdir();(parts/'0').write_bytes(raw[:3])
   def response(req,timeout):
    self.assertEqual(req.get_header('Range'),'bytes=3-12');return Response(raw[3:],3)
   with patch.object(d.urllib.request,'urlopen',side_effect=response):d.download('https://example.invalid/model',p,len(raw),hashlib.sha256(raw).hexdigest())
   self.assertEqual(p.read_bytes(),raw);self.assertFalse(parts.exists())
 def test_bad_checksum_never_publishes_model(self):
  with tempfile.TemporaryDirectory() as folder,patch.object(d,'ROOT',Path(folder)),patch.object(d,'STATUS',Path(folder)/'status.json'):
   p=Path(folder)/'model.safetensors'
   with patch.object(d.urllib.request,'urlopen',return_value=Response(b'bad',0)):
    with self.assertRaisesRegex(RuntimeError,'model_checksum_mismatch'):d.download('https://example.invalid/model',p,3,'0'*64)
   self.assertFalse(p.exists())
if __name__=='__main__':unittest.main()
