"""Independent browser grading only when native is idle and bytes match deployment."""
import fcntl,hashlib,importlib.util,json,subprocess,time,sys
from pathlib import Path
REPO=Path(__file__).resolve().parents[1];R=Path(Path('/tmp/harmonika-level12-root.path').read_text().strip());lock=(R/'native.lock').open('a');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB);meta=json.loads((R/'project-meta.json').read_text())
if not all(hashlib.sha256((R/'project'/n).read_bytes()).hexdigest()==sha for n,sha in meta['protected'].items()):raise RuntimeError('protected_changed')
spec=importlib.util.spec_from_file_location('operator_controller',REPO/'scripts/level12-lab/controller.py');O=importlib.util.module_from_spec(spec);spec.loader.exec_module(O);status=O.remote({'op':'status'});manifest=status['current']
if not status['pid_bound_ready'] or not all(hashlib.sha256((R/'project'/n).read_bytes()).hexdigest()==sha for n,sha in manifest['hashes'].items()):raise RuntimeError('deployment_source_mismatch')
p=subprocess.run(['node',str(REPO/'scripts/level12-ui-grade.cjs')]+(['--revision'] if '--revision' in sys.argv else []),capture_output=True,text=True,timeout=180);at=str(time.time_ns());(R/('ui-grade-'+at+'.stdout')).write_text(p.stdout);(R/('ui-grade-'+at+'.stderr')).write_text(p.stderr)
result=json.loads(next(l for l in reversed(p.stdout.splitlines()) if l.startswith('{')));result['deployed_release']=manifest['release'];result['source_sha256']=manifest['source_sha256'];(R/('ui-grade-'+at+'.json')).write_text(json.dumps(result,indent=2));print(json.dumps(result));raise SystemExit(p.returncode)
