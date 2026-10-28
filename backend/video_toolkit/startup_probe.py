"""Load WanGP API/defaults offline without generating a video."""
import json,os,sys,traceback
from pathlib import Path
CONTROL=Path(__file__).resolve().parent;ROOT=Path(r'D:\HarmonikaAI\video-toolkit');APP=Path(r'C:\HarmonikaVideo\wangp\app')
os.environ.update(HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1',HF_HUB_DISABLE_TELEMETRY='1',DO_NOT_TRACK='1')
sys.path.insert(0,str(APP));os.chdir(APP)
try:
 from shared.api import WanGPSession
 session=WanGPSession(root=APP,config_path=CONTROL/'wgp_config.json',cli_args=['--offline','--attention','sdpa','--profile','4'],console_output=False,console_isatty=False)
 settings=session.get_default_settings('t2v_1.3B')
 result={'status':'ready','model_definition':'t2v_1.3B','defaults_keys':len(settings),'setting_keys':sorted(settings),'inference_verified':False}
except Exception as exc:
 frames=traceback.extract_tb(exc.__traceback__);last=frames[-1] if frames else None
 result={'status':'failed','error_type':type(exc).__name__,'function':last.name if last else None,'line':last.lineno if last else None,'missing_module':getattr(exc,'name',None)}
(CONTROL/'wangp-startup.json').write_text(json.dumps(result,indent=2))
