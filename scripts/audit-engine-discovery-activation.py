#!/usr/bin/env python3
"""Read-only checks plus removal of this run's exact execution fixture row."""
import hashlib,json,sqlite3,subprocess
from pathlib import Path
base=Path('/opt/harmonika-ai');stage=base/'discovery-stage'
assert json.loads((base/'discovery-public-smoke.json').read_text())['status']=='passed'
assert not list((base/'skills-runtime').glob('zz-discovery-probe-*'))
for file in (stage/'engine_registry').rglob('*.py'):
 if '__pycache__' in file.parts:continue
 assert file.read_bytes()==(base/'engine_registry'/file.relative_to(stage/'engine_registry')).read_bytes(),file.name
assert (stage/'master_engine/service.py').read_bytes()==(base/'master_engine/service.py').read_bytes()
other=json.loads((stage/'pids.json').read_text())
assert other=={s:subprocess.check_output(['systemctl','show',s,'-p','MainPID','--value'],text=True).strip() for s in other}
for name,digest in json.loads((stage/'baseline.json').read_text()).items():
 if name.endswith('/service.py'):continue
 assert hashlib.sha256(Path(name).read_bytes()).hexdigest()==digest,name
with sqlite3.connect('/var/lib/harmonika-master/discovery.sqlite3') as db:
 removed=db.execute('DELETE FROM engine_discovery_execution WHERE skill_id=?',('diagnostic.discovery_probe',)).rowcount
 assert db.execute('SELECT count(*) FROM engine_discovery_status WHERE error_code != ?',('OK',)).fetchone()[0]==0
 assert db.execute('PRAGMA quick_check').fetchone()[0]=='ok'
p=base/'discovery-activation.json';report=json.loads(p.read_text());report.update(status='active_verified',public_acceptance='passed',fixture_plugin_directories_removed=True,fixture_execution_rows_removed=removed,database_integrity='ok',active_source_matches_tested=True)
p.write_text(json.dumps(report,indent=2));print(json.dumps(report))
