#!/usr/bin/env python3
"""Baseline and isolated paths for discovery scalability upgrade."""
import hashlib,json,os,shutil,subprocess
from pathlib import Path
B=Path('/opt/harmonika-ai');S=B/'discovery-scale-stage';W=Path('/srv/harmonika-web-chat/current').resolve()
assert os.geteuid()==0;S.mkdir(mode=0o700,exist_ok=True)
files=[B/'master_engine/service.py',W/'harmonika-web',Path('/etc/systemd/system/harmonika-master-skills.service.d/discovery.conf')]+list((B/'engine_registry').glob('*.py'))
(S/'baseline.json').write_text(json.dumps({str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in files}))
others=['harmonika-ai','harmonika-shared-core','harmonika-coding-runtime','harmonika-telco-engine','harmonika-legal-engine','harmonika-legal-media','harmonika-web-builder','harmonika-unified','harmonika-web-chat-live']
(S/'pids.json').write_text(json.dumps({s:subprocess.check_output(['systemctl','show',s,'-p','MainPID','--value'],text=True).strip() for s in others}))
for name in ('master_engine','engine_registry'):
 assert not (S/name).exists(),'stage_already_prepared'
 shutil.copytree(B/name,S/name,ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
(S/'skills-runtime').mkdir(mode=0o755);shutil.copytree(B/'skills-runtime/text-metrics',S/'skills-runtime/text-metrics')
for name in ('state','tests','runtime/tools','gateway'):(S/name).mkdir(parents=True,exist_ok=True)
(S/'tests/__init__.py').touch()
for p in W.iterdir():
 if p.suffix=='.go' or p.name in ('go.mod','go.sum'):shutil.copy2(p,S/'gateway'/p.name)
print(json.dumps({'status':'prepared','production_changed':False}))
