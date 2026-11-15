"""Seal an independently accepted deployed baseline before native revision work."""
import argparse, fcntl, hashlib, importlib.util, json, shutil
from pathlib import Path

p=argparse.ArgumentParser();p.add_argument('--ui',type=Path,required=True);p.add_argument('--domain',type=Path,required=True);a=p.parse_args()
REPO=Path(__file__).resolve().parents[1];R=Path(Path('/tmp/harmonika-level12-root.path').read_text().strip());P=R/'project'
lock=(R/'native.lock').open('a');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
meta=json.loads((R/'project-meta.json').read_text());state=json.loads((R/'WORK-STATE.json').read_text())
assert state['phase']=='baseline'
assert all(hashlib.sha256((P/n).read_bytes()).hexdigest()==sha for n,sha in meta['protected'].items())
ui=json.loads(a.ui.read_text());domain=json.loads(a.domain.read_text());visual=json.loads((R/'baseline-visual-approval.json').read_text())
spec=importlib.util.spec_from_file_location('controller',REPO/'scripts/level12-lab/controller.py');O=importlib.util.module_from_spec(spec);spec.loader.exec_module(O)
st=O.remote({'op':'status'});m=st['current'];checkpoint=json.loads((P/'CHECKPOINT.json').read_text())
assert st['pid_bound_ready'] and checkpoint['source_sha256']==m['source_sha256'] and checkpoint['release']==m['release']
assert ui['passed'] and ui['source_sha256']==m['source_sha256'] and domain['passed'] and domain['cases']==11
assert visual['passed'] and visual['source_sha256']==m['source_sha256']
assert all(hashlib.sha256((P/n).read_bytes()).hexdigest()==sha for n,sha in m['hashes'].items())
result=json.loads((P/'RESULT.json').read_text());assert result['phase']=='baseline' and result.get('completed') is True
anchor=O.api({'method':'GET','path':'/api/customers/'+checkpoint['baseline_customer_id'],'role':'admin'})
assert anchor['status']==200
dest=P/'reference/v1';assert not dest.exists();dest.mkdir(parents=True)
for n in m['hashes']:
    target=dest/n;target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(P/n,target)
shutil.copyfile(P/'tests/test_public.py',dest/'test_public.py')
for f in dest.rglob('*'):
    if f.is_file():meta['protected'][str(f.relative_to(P))]=hashlib.sha256(f.read_bytes()).hexdigest()
meta['protected']['CHECKPOINT.json']=hashlib.sha256((P/'CHECKPOINT.json').read_bytes()).hexdigest()
meta['baseline_release']=m['release'];meta['baseline_source_sha256']=m['source_sha256']
(R/'project-meta.json').write_text(json.dumps(meta,indent=2))
state.update(phase='revision_recovery',schema_phase='v2',baseline_independent_passed=True)
(R/'WORK-STATE.json').write_text(json.dumps(state,indent=2))
acceptance={'passed':True,'release':m['release'],'source_sha256':m['source_sha256'],'domain_cases':11,'ui_checks':len(ui['checks']),'reference_sealed':True,'checkpoint_sha256':meta['protected']['CHECKPOINT.json']}
(R/'baseline-acceptance.json').write_text(json.dumps(acceptance,indent=2));print(json.dumps(acceptance))
