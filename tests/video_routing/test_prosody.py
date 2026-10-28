import base64,hashlib,importlib.util,io,re,unittest,wave
from pathlib import Path
s=importlib.util.spec_from_file_location('prosody',Path(__file__).resolve().parents[2]/'backend/video_routing/prosody.py');m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
def clip():
 out=io.BytesIO()
 with wave.open(out,'wb') as w:w.setnchannels(1);w.setsampwidth(2);w.setframerate(44100);w.writeframes(b'\x00\x01'*4410)
 raw=out.getvalue();return {'ok':True,'bytes':len(raw),'audio_base64':base64.b64encode(raw).decode(),'sha256':hashlib.sha256(raw).hexdigest(),'voice_quality':'neural_synthetic'}
class Prosody(unittest.TestCase):
 def test_preserves_words_and_bounds_phrases(self):
  text='Halo, Kak! Mau tanya internet? Ini Harmonika. Silakan cek lokasi. Terima kasih!';parts=m.phrases(text);self.assertEqual(len(parts),3);self.assertEqual(re.findall(r'\w+',' '.join(parts)),re.findall(r'\w+',text))
 def test_real_wav_with_bounded_pause_and_consistent_voice(self):
  seen=[]
  def call(path,data):seen.append(data);return clip()
  result=m.promotional('Halo, Kak! Yuk, cek paket Harmonika.','id','pria',1.08,call)
  self.assertEqual([x['voice'] for x in seen],['pria','pria']);self.assertFalse(result['native_emotion_control']);self.assertEqual(result['style'],'promosi_ceria');self.assertAlmostEqual(result['duration_seconds'],.36,places=2)
 def test_rejects_long_or_bad_artifacts(self):
  with self.assertRaises(ValueError):m.promotional('x'*601,'id','pria',1.08,lambda *a:clip())
  bad=clip();bad['sha256']='0'*64
  with self.assertRaisesRegex(ValueError,'invalid_audio'):m.promotional('Halo.','id','pria',1.08,lambda *a:bad)
if __name__=='__main__':unittest.main()
