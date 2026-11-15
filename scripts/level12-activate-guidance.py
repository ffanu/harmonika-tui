"""Activate Level12 evidence guidance after independently verified revision/recovery."""
import fcntl,json,shlex,subprocess
from pathlib import Path
REPO=Path(__file__).resolve().parents[1];R=Path(Path('/tmp/harmonika-level12-root.path').read_text().strip());state=json.loads((R/'WORK-STATE.json').read_text());assert state.get('revision_independent_passed') is True, 'revision_acceptance_required';lock=(R/'native.lock').open('a');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB);stage='/opt/harmonika-ai/level12-guidance-final-'+json.loads((R/'lab.json').read_text())['lab_id'];old=json.loads((R/'overflow-activation-result.json').read_text())['activation']['candidate_sha256'];attempt=R/'guidance-activation-attempt.json'
if attempt.exists():raise RuntimeError('activation_already_attempted_reconcile_stage_state')
attempt.write_text(json.dumps({'stage':stage,'expected':old,'state':'attempted_not_confirmed'},indent=2))
code=r'''import importlib.util,json,sys,subprocess
from pathlib import Path
r=json.load(sys.stdin);stage=Path(r['stage']);spec=importlib.util.spec_from_file_location('activation',stage/'scripts/activate-source-reference.py');a=importlib.util.module_from_spec(spec);spec.loader.exec_module(a);a.OLD=r['expected'];sys.argv=[str(stage/'scripts/activate-source-reference.py'),str(stage)];a.main()
p=subprocess.run(['node',str(stage/'scripts/source-reference-smoke.cjs'),str(stage)],capture_output=True,text=True,timeout=900);(stage/'level12-post-source.output').write_text(p.stdout+p.stderr);rows=json.loads((stage/'source-after.json').read_text());value={'activation':json.loads((stage/'activation.json').read_text()),'source_canaries_passed':p.returncode==0 and len(rows)==5 and all(v['passed'] and not v['client_tools_executed'] and not v['internal_state_exposed'] for v in rows),'source_canaries':rows};(stage/'level12-guidance-result.json').write_text(json.dumps(value,indent=2));print(json.dumps(value));sys.exit(not value['source_canaries_passed'])
'''
p=subprocess.run(['ssh','-o','BatchMode=yes','-o','StrictHostKeyChecking=yes','vps','python3 -c '+shlex.quote(code)],input=json.dumps({'stage':stage,'expected':old}),capture_output=True,text=True,timeout=1000);(R/'guidance-activation.stdout').write_text(p.stdout);(R/'guidance-activation.stderr').write_text(p.stderr)
try:
 rows=[json.loads(l) for l in p.stdout.splitlines() if l.startswith('{')];value=rows[-1];(R/'guidance-activation-result.json').write_text(json.dumps(value,indent=2));print(json.dumps({'status':value['activation']['status'],'source_canaries_passed':value['source_canaries_passed'],'health_http':value['activation'].get('health_http')}))
except (ValueError,IndexError,KeyError):print(json.dumps({'exit_code':p.returncode,'state':'reconcile_required'}))
raise SystemExit(p.returncode)
