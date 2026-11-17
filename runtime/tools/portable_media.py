"""Portable public media route; private PC connectors remain device-local."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess


def _output(args, suffix):
    root=Path(os.environ.get('HARMONIKA_DOWNLOADS_DIR',str(Path.home()/'Downloads/harmonika-ai')))/'gambar'
    root.mkdir(parents=True,exist_ok=True)
    fingerprint=hashlib.sha256(json.dumps(args,sort_keys=True,ensure_ascii=False).encode()).hexdigest()[:20]
    return root/(suffix+'-'+fingerprint+'.png')


def _call(name, args):
    node=os.environ.get('HARMONIKA_NODE') or shutil.which('node')
    if not node:return {'ok':False,'error':'node_runtime_unavailable'}
    try:
        result=subprocess.run([node,str(Path(__file__).with_name('portable-media.cjs'))],
            input=json.dumps({'name':name,'arguments':args}).encode(),capture_output=True,timeout=190)
        if len(result.stdout)>1024*1024:return {'ok':False,'error':'media_response_limit','retry_safe':False}
        value=json.loads(result.stdout)
        return value if isinstance(value,dict) else {'ok':False,'error':'invalid_media_response','retry_safe':False}
    except subprocess.TimeoutExpired:return {'ok':False,'error':'media_state_unknown','retry_safe':False}
    except (OSError,ValueError):return {'ok':False,'error':'media_adapter_unavailable','retry_safe':False}


def generate(args):
    output=_output(args,'image')
    if output.is_file() and not output.is_symlink():
        raw=output.read_bytes()
        if raw.startswith(b'\x89PNG\r\n\x1a\n'):
            return {'ok':True,'file':str(output),'output_file':str(output),'existing_artifact':True,'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest()}
        return {'ok':False,'error':'existing_output_invalid'}
    value=_call('generate_image',{'prompt':args.get('prompt',''),'output_file':str(output)})
    if value.get('ok'):value['file']=value.get('output_file',str(output))
    return value


def analyze(args):
    return _call('analyze_image',{'file_path':args.get('path',''),'prompt':args.get('prompt') or 'Describe the image accurately; mark unreadable details as uncertain.'})


def transcribe(args):
    return _call('transcribe_audio',{'file_path':args.get('path','')})


def upscale(args):
    return _call('upscale_image',{'file_path':args.get('path',''),'output_file':str(_output(args,'upscale'))})


def install(handlers):
    # An owner's already-configured local workers stay selectable, while a new
    # device needs only its own Harmonika license for the public gateway route.
    if not os.environ.get('HARMONIKA_VISION_TOKEN'):
        handlers.update({'generate_image':generate,'generate_fluxhd':generate,'analyze_image':analyze,
                         'transcribe_audio':transcribe,'upscale_image':upscale})
