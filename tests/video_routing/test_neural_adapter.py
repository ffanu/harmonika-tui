import importlib.util,unittest
from pathlib import Path
from unittest.mock import patch
s=importlib.util.spec_from_file_location('speech',Path(__file__).resolve().parents[2]/'backend/video_routing/speech.py');m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
class NeuralAdapter(unittest.TestCase):
 def test_default_routes_neural_without_basic_subprocess(self):
  with patch.object(m,'policy',return_value={'default_quality':'neural','default_voice':'wanita'}),patch.object(m,'neural_call',return_value={'ok':True}) as call,patch.object(m,'normalize_neural',side_effect=lambda x:x),patch.object(m.subprocess,'run') as basic:
   self.assertTrue(m.generate({'text':'fixture','language':'id'})['ok']);self.assertEqual(call.call_args.args[1]['voice'],'wanita');basic.assert_not_called()
 def test_neural_error_never_silently_downgrades(self):
  with patch.object(m,'policy',return_value={'default_quality':'neural','default_voice':'pria'}),patch.object(m,'neural_call',side_effect=m.SpeechFailure('neural_speech_unavailable')),patch.object(m.subprocess,'run') as basic:
   with self.assertRaisesRegex(m.SpeechFailure,'neural_speech_unavailable'):m.generate({'text':'fixture'})
   basic.assert_not_called()
 def test_explicit_voice_and_quality_are_validated(self):
  for data in [{'text':'fixture','voice':'../../secret'},{'text':'fixture','quality':'other'}]:
   with self.assertRaisesRegex(m.SpeechFailure,'invalid_speech_options'):m.generate(data)
if __name__=='__main__':unittest.main()
