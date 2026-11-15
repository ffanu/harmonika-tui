#!/usr/bin/env python3
"""Seal actual final results, then activate the coding service through pinned SSH."""
import argparse,base64,hashlib,json,shlex,subprocess
from pathlib import Path

REPO=Path(__file__).resolve().parents[1];R=Path(Path('/tmp/harmonika-level14-root.path').read_text().strip());FILES=('agent.py','core.py','service.py','sandbox.py','action_evidence.py')
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def read(p):return json.loads(p.read_text())

def main():
    p=argparse.ArgumentParser();p.add_argument('--attempt',type=int,default=4);a=p.parse_args()
    source={n:sha(REPO/'backend/coding_runtime'/n) for n in FILES}
    for name,h in read(R/'sealed-source.json').items():
        if name.startswith(('backend/','tests/')):assert sha(REPO/name)==h,'source_changed_after_linux_tests'
    evidence=[]
    for n in range(1,10):
        f=R/('N'+str(n).zfill(2)+'-attempt'+str(a.attempt)+'.json');reconciled=f.with_name(f.stem+'-reconciled.json')
        if reconciled.exists():
            assert not read(f)['passed'] and read(reconciled)['extra']['original_failure_sha256']==sha(f),'reconciliation_binding'
            f=reconciled
        v=read(f);assert v['passed'],'native_case_failed'
        assert all(v['source_sha256']['backend/coding_runtime/'+name]==h for name,h in source.items()),'native_source_changed'
        if n==3:assert v['turns'][0]['sse_done'] is True
        evidence.append(f)
    for name in ('final-level14-linux-tests.txt','final-coding-regressions.txt','final-phase2-regressions.txt'):
        f=R/name;text=f.read_text();assert '\nOK' in text and '\nFAILED' not in text;evidence.append(f)
    assert '--- PASS: TestLevel14VerifiedPrincipalOverridesCallerHeaders' in (R/'go-bridge-tests.txt').read_text()
    executor=read(R/'candidate-executor.json');assert executor['passed'] and executor['source_sha256']==source
    assert read(R/'review-resolution.json')['addressed']
    assert read(R/'native-level14-compat-low/result.json')['passed']
    evidence.extend(R/n for n in ('go-bridge-tests.txt','candidate-executor.json','review-resolution.json'))
    prior=read(R/'coding-baseline.json')['actual'];companions=read(R/'production-preflight.json')['sha256'];companions={n:h for n,h in companions.items() if n not in FILES}
    manifest={'candidate_sha256':source,'prior_sha256':{n:prior.get(n) for n in FILES},'companion_sha256':companions,'linux_tests_passed':True,'activation_guard_tests_passed':True,'go_bridge_passed':True,'independent_review_addressed':True,'native_missions_passed':True,'candidate_api_sse_passed':True,'native_count':9,'adversarial_count':36,'executor_image':executor['image'],'executor_image_id':executor['image_id'],'evidence_sha256':{f.name:sha(f) for f in evidence}}
    stage='/opt/harmonika-ai/level14-evidence-stage';payload={'manifest':manifest,'evidence':{f.name:base64.b64encode(f.read_bytes()).decode() for f in evidence},'scripts':{n:base64.b64encode((REPO/'scripts'/n).read_bytes()).decode() for n in ('level14-activate.py','level14-live-canary.py')}}
    code='''from pathlib import Path
import sys,json,base64,subprocess
v=json.load(sys.stdin);root=Path('/opt/harmonika-ai/level14-evidence-stage');r=root/'level14-evidence';r.mkdir(mode=0o700,exist_ok=True)
for name,b in v['evidence'].items():
 p=r/name;p.write_bytes(base64.b64decode(b));p.chmod(0o600)
for name,b in v['scripts'].items():
 p=root/'scripts'/name;p.write_bytes(base64.b64decode(b));p.chmod(0o600)
p=root/'level14-release.json';p.write_text(json.dumps(v['manifest'],indent=2));p.chmod(0o600)
x=subprocess.run(['python3',str(root/'scripts/level14-activate.py'),str(root)],text=True,capture_output=True);(root/'level14-activation-output.txt').write_text(x.stdout+x.stderr)
if x.returncode:print(json.dumps({'activation_failed':True,'reconciliation_required':True}));raise SystemExit(1)
print((root/'level14-activation.json').read_text())
'''
    x=subprocess.run(['ssh','-o','BatchMode=yes','-o','StrictHostKeyChecking=yes','vps','python3 -c '+shlex.quote(code)],input=json.dumps(payload),capture_output=True,text=True)
    (R/'activation-transport.txt').write_text(x.stdout+x.stderr)
    if x.returncode:raise RuntimeError('activation_failed_reconcile_without_replay')
    value=json.loads(x.stdout);(R/'level14-activation.json').write_text(json.dumps(value,indent=2));(R/'level14-release.json').write_text(json.dumps(manifest,indent=2));print(json.dumps({'status':value['status'],'coding_pid':value.get('new_pid'),'unrelated_pids_unchanged':value.get('unrelated_service_pids_unchanged')}))

if __name__=='__main__':main()
