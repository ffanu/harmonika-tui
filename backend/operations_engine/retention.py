"""Expire only known owned browser artifacts. Never follows links or opens traces."""
import json,os,re,stat,time
from pathlib import Path
from .common import private_path
SCOPE=re.compile(r'^[a-f0-9]{64}$');RUN=re.compile(r'^browser_[a-f0-9]{32}$')
def prune(root,now=None):
 now=int(time.time() if now is None else now);base=private_path(Path(root)/'artifacts',directory=True);removed=0;examined=0;errors=0
 for scope in base.iterdir():
  if not SCOPE.fullmatch(scope.name) or scope.is_symlink() or not scope.is_dir():continue
  try:private_path(scope,directory=True)
  except (ValueError,OSError):continue
  for run in scope.iterdir():
   if examined>=1000:return {'examined':examined,'removed':removed,'errors':errors,'limit_reached':True}
   if not RUN.fullmatch(run.name) or run.is_symlink() or not run.is_dir():continue
   examined+=1
   try:
    private_path(run,directory=True);meta=private_path(run/'retention.json');files=list(run.iterdir())
    if meta.stat().st_size>1024 or any(f.name not in ('retention.json','trace.zip','screenshot.png') or f.is_symlink() or not f.is_file() for f in files):continue
    value=json.loads(meta.read_text());start=value['created_at'];end=value['expires_at']
    if type(start) is not int or type(end) is not int or end-start!=86400 or end>now:continue
    for file in files:private_path(file)
    for file in files:file.unlink()
    run.rmdir();removed+=1
   except (ValueError,OSError,KeyError):errors+=1
 return {'examined':examined,'removed':removed,'errors':errors,'limit_reached':False}
