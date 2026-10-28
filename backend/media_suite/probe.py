"""Package checks are not inference qualification."""
import importlib.metadata as md,json,sys
op=sys.argv[1]
if op=='asr':from faster_whisper import WhisperModel
elif op=='upscale':
 from aura_sr import AuraSR
 import torch
 assert torch.cuda.is_available(),'gpu_offline'
elif op=='tts':from f5_tts.api import F5TTS
elif op=='browser':
 from browser_use import BrowserSession,BrowserProfile
 from browser_use.browser.events import NavigateToUrlEvent
else:raise ValueError('invalid_probe')
print(json.dumps({'component':op,'import_ok':True,'real_test_passed':False}))
