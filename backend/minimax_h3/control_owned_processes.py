"""Inspect/stop only exact scripts started by this H3 installation, never other workers."""
import json,os,sys
from pathlib import Path
for root in ['harmonika-flux-worker','harmonika-image-worker','harmonika-vision-worker']:
 site=Path('C:/Users/Kraken')/root/'.venv/Lib/site-packages'
 if (site/'psutil').is_dir():sys.path.insert(0,str(site));break
import psutil
allowed={'d:/harmonikaai/minimaxh3/download_models.py','d:/harmonikaai/minimaxh3/provision_source.py','d:/harmonikaai/minimaxh3/install_torch.py'}
rows=[]
for p in psutil.process_iter(['pid','name']):
 try:
  if p.info['name'].lower()!='python.exe':continue
  args=p.cmdline() or []
  paths={os.path.normcase(os.path.normpath(a)).replace('\\','/').lower() for a in args if a.lower().endswith('.py')}
  if p.info['name'].lower()!='python.exe' or not paths&allowed:continue
  row={'owned_pid':p.pid,'script':next(iter(paths&allowed)).split('/')[-1]}
  if '--stop' in sys.argv:p.terminate();row['stop_requested']=True
  rows.append(row)
 except (psutil.NoSuchProcess,psutil.AccessDenied):pass
print(json.dumps({'owned_install_processes':rows,'other_processes_modified':False}),flush=True)
