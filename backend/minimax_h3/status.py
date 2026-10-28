"""Bounded technical installation state; never reads input prompts or credentials."""
import json,time
from pathlib import Path
ROOT=Path(__file__).resolve().parent
for name in ['bootstrap-status.json','model-download-status.json','torch-download-status.json','coordinator-status.json','qualification-status.json']:
 try:
  p=ROOT/name
  if p.stat().st_size>65536:raise ValueError('status_size_limit')
  value=json.loads(p.read_text(encoding='utf-8-sig'));print(json.dumps({'file':name,'status':value}),flush=True)
 except FileNotFoundError:print(json.dumps({'file':name,'missing':True}),flush=True)
 except Exception as e:print(json.dumps({'file':name,'error_type':type(e).__name__}),flush=True)

import sys
if '--tail' in sys.argv:
 try:
  with (ROOT/'bootstrap-task.log').open('rb') as f:
   f.seek(0,2);size=f.tell();offset=max(0,size-8000);offset-=offset%2;f.seek(offset);raw=f.read(size-offset)
  print(json.dumps({'bootstrap_tail':raw.decode('utf-16le' if b'\x00' in raw else 'utf-8',errors='replace')[-5000:]}),flush=True)
 except Exception as exc:print(json.dumps({'bootstrap_log_error_type':type(exc).__name__}),flush=True)
