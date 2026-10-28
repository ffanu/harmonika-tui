import base64,importlib.util,io,math,shutil,struct,subprocess,tempfile,unittest,wave
from pathlib import Path
s=importlib.util.spec_from_file_location('narrate',Path(__file__).resolve().parents[2]/'backend/video_routing/narrate.py');m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
class NarrateTests(unittest.TestCase):
 def test_no_truncating_speech(self):
  with self.assertRaisesRegex(m.NarrationFailure,'narration_too_long'):m.combine(b'',{'duration_seconds':3},2)
 @unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'),'ffmpeg required')
 def test_real_mux_preserves_duration_and_audio(self):
  with tempfile.TemporaryDirectory() as temp:
   p=Path(temp)/'fixture.mp4';subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','color=c=blue:s=160x96:r=16:d=2','-c:v','libx264','-pix_fmt','yuv420p',str(p)],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
   wav=io.BytesIO()
   with wave.open(wav,'wb') as w:w.setnchannels(1);w.setsampwidth(2);w.setframerate(22050);w.writeframes(b''.join(struct.pack('<h',int(5000*math.sin(i*2*math.pi*220/22050))) for i in range(22050)))
   out=m.combine(p.read_bytes(),{'audio_base64':base64.b64encode(wav.getvalue()).decode(),'duration_seconds':1},2)
   self.assertTrue(out['audio']);self.assertEqual(out['duration_seconds'],2);self.assertEqual(out['width'],160)
if __name__=='__main__':unittest.main()
