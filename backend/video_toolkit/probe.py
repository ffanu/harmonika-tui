"""Import/CUDA check only, not real generation readiness."""
import importlib,json,os,sys
from pathlib import Path
name=sys.argv[1];root=Path(r'D:\HarmonikaAI\video-toolkit');sys.path.insert(0,str(Path(r'C:\HarmonikaVideo')/name/'app'))
import torch
mods={'framepack':['diffusers','transformers','gradio','av','diffusers_helper.models.hunyuan_video_packed'], 'mmaudio':['mmaudio.model.networks','mmaudio.eval_utils','mmaudio.model.utils.features_utils'], 'wangp':['diffusers','transformers','gradio','mmgp','models.wan.modules.model']}
for mod in mods[name]:importlib.import_module(mod)
assert torch.cuda.is_available(),'cuda_unavailable'
x=torch.ones((32,32),device='cuda',dtype=torch.bfloat16);y=x@x;assert y[0,0].item()==32
out={'engine':name,'imports_ok':True,'cuda_available':True,'torch':torch.__version__,'gpu':torch.cuda.get_device_name(0),'inference_verified':False}
(Path(__file__).resolve().parent/(name+'-probe.json')).write_text(json.dumps(out,indent=2));print(json.dumps(out))
