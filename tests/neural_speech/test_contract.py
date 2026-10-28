import importlib.util,unittest
from pathlib import Path
s=importlib.util.spec_from_file_location('neural_server',Path(__file__).resolve().parents[2]/'backend/neural_speech/server.py');m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
class Contract(unittest.TestCase):
 def test_voice_presets_only(self):
  for voice in ['wanita','pria','wanita-2','pria-2']:self.assertEqual(m.validate({'text':'fixture','voice':voice})[2],voice)
  for voice in ['../../secret','https://example.com/voice.wav','celebrity']:
   with self.assertRaises(ValueError):m.validate({'text':'fixture','voice':voice})
 def test_bounds(self):
  for data in [{'text':''},{'text':'x'*1201},{'text':'x\x00'},{'text':'x','speed':9},{'text':'x','language':'xx'},{'text':'x','reference_url':'secret'}]:
   with self.assertRaises(ValueError):m.validate(data)
if __name__=='__main__':unittest.main()
