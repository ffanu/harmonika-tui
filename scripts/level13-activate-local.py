#!/usr/bin/env python3
"""Operator: use recorded Level12 baseline; activate only exact live-tested Level13 source."""
import base64,fcntl,json,shlex,subprocess
from pathlib import Path
repo=Path(__file__).resolve().parents[1];r=Path(Path('/tmp/harmonika-level13-root.path').read_text().strip());stage='/opt/harmonika-ai/level13-20261115-proof';lock=(r/'native.lock').open('a');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
old=json.loads((repo/'docs/LEVEL12-AGENT-EXAM-20261115.json').read_text())['engine_activation']['candidate_sha256'];assert len(old)==8
code='''from pathlib import Path
import sys,json,base64,subprocess,hashlib
v=json.load(sys.stdin);stage=Path(v['stage']);base=Path('/opt/harmonika-ai/api')
assert all(hashlib.sha256((base/n).read_bytes()).hexdigest()==h for n,h in v['expected'].items()),'baseline_changed'
assert not (base/'proof-contract.cjs').exists(),'already_present_reconcile'
(stage/'expected-baseline.json').write_text(json.dumps(v['expected']));p=stage/'scripts/level13-activate.py';p.write_bytes(base64.b64decode(v['script']));p.chmod(0o600)
x=subprocess.run(['python3',str(p),str(stage)],capture_output=True,text=True);(stage/'activation-output.txt').write_text(x.stdout+x.stderr)
if x.returncode:print(json.dumps({'status':'activation_failed','reconcile_required':True}));raise SystemExit(1)
print((stage/'activation.json').read_text())
'''
x=subprocess.run(['ssh','-o','BatchMode=yes','-o','StrictHostKeyChecking=yes','vps','python3 -c '+shlex.quote(code)],input=json.dumps({'stage':stage,'expected':old,'script':base64.b64encode((repo/'scripts/level13-activate.py').read_bytes()).decode()}),capture_output=True,text=True);(r/'activation.stdout').write_text(x.stdout);(r/'activation.stderr').write_text(x.stderr)
if x.returncode:print(json.dumps({'status':'activation_failed','exit':x.returncode}));raise SystemExit(x.returncode)
v=json.loads(x.stdout);(r/'activation.json').write_text(json.dumps(v,indent=2));print(json.dumps({'status':v['status'],'health_http':v.get('health_http'),'unrelated_service_pids_unchanged':v.get('unrelated_service_pids_unchanged')}))
