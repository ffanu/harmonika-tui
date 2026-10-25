"""Explicit plain-text clipboard adapter, no implicit OSC52 or clipboard reads."""
import argparse
import json
import re
import shutil
import subprocess
import sys
MAX_BYTES=1048576
ANSI=re.compile(r'\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b\[[0-?]*[ -/]*[@-~]')
def adapter():
    for executable,args in [('pbcopy',[]),('wl-copy',[]),('xclip',['-selection','clipboard'])]:
        path=shutil.which(executable)
        if path:return [path,*args]
    return None
def copy_text(data,run=subprocess.run,command=None):
    if len(data)>MAX_BYTES:raise ValueError('clipboard_input_too_large')
    text=data.decode('utf8',errors='strict');text=ANSI.sub('',text)
    text=''.join(c for c in text if c in '\n\t' or (ord(c)>=32 and not 0x7f<=ord(c)<=0x9f))
    command=command or adapter()
    if not command:raise ValueError('clipboard_unavailable')
    result=run(command,input=text.encode(),capture_output=True,timeout=3)
    if result.returncode:raise ValueError('clipboard_failed')
    return {'ok':True,'copied_bytes':len(text.encode()),'provider':'Harmonika Ai'}
def main():
    parser=argparse.ArgumentParser();parser.add_argument('action',choices=['status','copy']);args=parser.parse_args()
    try:
        result={'ok':True,'available':bool(adapter()),'osc52':False} if args.action=='status' else copy_text(sys.stdin.buffer.read(MAX_BYTES+1))
        print(json.dumps(result))
    except Exception:print(json.dumps({'ok':False,'error':'clipboard_unavailable_or_invalid_input'}));return 2
    return 0
if __name__=='__main__':sys.exit(main())
