#!/usr/bin/env python3
"""Prepare isolated validation paths. No production mutation or secret output."""
import hashlib,json,os,shutil,subprocess
from pathlib import Path
BASE=Path('/opt/harmonika-ai');STAGE=BASE/'discovery-stage';WEB=Path('/srv/harmonika-web-chat/current').resolve()
assert os.geteuid()==0
STAGE.mkdir(mode=0o700,exist_ok=True)
files=[BASE/'master_engine/service.py',BASE/'master_engine/index.py',Path('/etc/systemd/system/harmonika-master-skills.service'),WEB/'harmonika-web']
(STAGE/'baseline.json').write_text(json.dumps({str(f):hashlib.sha256(f.read_bytes()).hexdigest() for f in files}))
services=['harmonika-ai','harmonika-shared-core','harmonika-coding-runtime','harmonika-telco-engine','harmonika-legal-engine','harmonika-legal-media','harmonika-web-builder','harmonika-unified','harmonika-web-chat-live']
(STAGE/'pids.json').write_text(json.dumps({s:subprocess.check_output(['systemctl','show',s,'-p','MainPID','--value'],text=True).strip() for s in services}))
(STAGE/'gateway').mkdir(exist_ok=True)
for source in WEB.iterdir():
 if source.name in ('go.mod','go.sum') or source.suffix=='.go':shutil.copy2(source,STAGE/'gateway'/source.name)
# Existing module copied only to stage; overlay the reviewed service separately.
if not (STAGE/'master_engine').exists():shutil.copytree(BASE/'master_engine',STAGE/'master_engine',ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
for name in ('skills-runtime','state','tests'):(STAGE/name).mkdir(mode=0o700,exist_ok=True)
(STAGE/'tests/__init__.py').touch()
print(json.dumps({'status':'prepared','production_changed':False,'stage':str(STAGE)}))
