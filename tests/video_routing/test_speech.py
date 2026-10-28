import importlib.util,io,unittest,wave,tempfile
from pathlib import Path
from unittest.mock import patch
s=importlib.util.spec_from_file_location('speech',Path(__file__).resolve().parents[2]/'backend/video_routing/speech.py');m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
class SpeechTests(unittest.TestCase):
 def test_validation(self):
  for args in [{'text':''},{'text':'x','language':'xx'},{'text':'x','speed':1},{'text':'x','command':'evil'}]:
   with self.assertRaises(m.SpeechFailure):m.generate({'operation':'speech_generate','owner_context':'fixture',**args})
 def test_speech_stdin_and_cleanup(self):
  paths=[]
  def fake(argv,**kwargs):
   self.assertNotIn('private sentence',argv);self.assertEqual(kwargs['input'],b'private sentence');out=Path(argv[argv.index('-w')+1]);paths.append(out)
   with wave.open(str(out),'wb') as w:w.setnchannels(1);w.setsampwidth(2);w.setframerate(22050);w.writeframes(b'\x00\x01'*22050)
   class Result:returncode=0
   return Result()
  with patch.object(m.shutil,'which',return_value='/usr/bin/espeak-ng'),patch.object(m.subprocess,'run',fake):out=m.generate({'operation':'speech_generate','text':'private sentence'})
  self.assertEqual(out['duration_seconds'],1);self.assertEqual(out['mime_type'],'audio/wav');self.assertFalse(paths[0].exists());self.assertNotIn('text',out)
 def test_missing_engine(self):
  with patch.object(m.shutil,'which',return_value=None):
   with self.assertRaisesRegex(m.SpeechFailure,'speech_unavailable'):m.generate({'text':'hello'})
if __name__=='__main__':unittest.main()
