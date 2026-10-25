#!/usr/bin/env python3
import hashlib,json,os,shutil,subprocess
from pathlib import Path
B=Path('/opt/harmonika-ai');S=B/'media-bridge-stage';W=Path('/srv/harmonika-web-chat/current').resolve();assert os.geteuid()==0
(S/'gateway').mkdir(exist_ok=True)
for p in W.iterdir():
 if p.suffix=='.go' or p.name in ('go.mod','go.sum'):shutil.copy2(p,S/'gateway'/p.name)
files=[W/'main.go',W/'public_api_docs.go',W/'harmonika-web',B/'rtx-queue/backend/rtx_queue/service.py']
(S/'baseline.json').write_text(json.dumps({str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in files}))
services=['harmonika-ai','harmonika-shared-core','harmonika-coding-runtime','harmonika-telco-engine','harmonika-legal-engine','harmonika-legal-media','harmonika-web-builder','harmonika-unified','harmonika-master-skills','harmonika-rtx-tunnel']
(S/'pids.json').write_text(json.dumps({s:subprocess.check_output(['systemctl','show',s,'-p','MainPID','--value'],text=True).strip() for s in services}))
assert not Path('/etc/harmonika-ai/rtx-public-bridge.token').exists(),'bridge_secret_already_exists'
print(json.dumps({'status':'prepared','production_changed':False}))
