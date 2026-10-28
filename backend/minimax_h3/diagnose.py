"""Mechanical installation diagnostics only. No user prompts or credentials."""
import json,os,sys
from pathlib import Path
import psutil
ROOT=Path('D:/HarmonikaAI/MiniMaxH3')
for name in ['bootstrap-status.json','model-download-status.json','torch-download-status.json']:
 p=ROOT/name
 try:print(json.dumps({'file':name,'value':json.loads(p.read_text())}),flush=True)
 except Exception as e:print(json.dumps({'file':name,'error_type':type(e).__name__}),flush=True)
for p in psutil.process_iter(['pid','name','cmdline','memory_info']):
 try:
  args=p.info['cmdline'] or []
  if p.info['name'].lower()=='python.exe' and any('minimaxh3' in a.lower() for a in args):
   files=[Path(a).name for a in args if a.endswith('.py')]
   print(json.dumps({'owned_pid':p.pid,'scripts':files,'rss':p.info['memory_info'].rss}),flush=True)
 except (psutil.NoSuchProcess,psutil.AccessDenied):pass
for name in ['bootstrap-task.log']:
 try:
  p=ROOT/name;b=p.read_bytes();text=b.decode('utf-16' if b.startswith(bytes([255,254])) else 'utf-8',errors='replace');print(json.dumps({'file':name,'tail':text[-3000:]}),flush=True)
 except Exception as e:print(json.dumps({'file':name,'error_type':type(e).__name__}),flush=True)
