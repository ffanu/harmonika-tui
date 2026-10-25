#!/usr/bin/env python3
"""Delete exact synthetic token fixtures after all jobs have stopped."""
import json,sys
from pathlib import Path
sys.path.insert(0,'/opt/harmonika-ai')
from unified_engine.state import State
from core_engine.state import Store
ids=json.load(sys.stdin)['actor_ids'];assert type(ids) is list and len(ids)<=4 and all(type(x) is int and x>0 for x in ids)
state=State('/var/lib/harmonika-unified/state.sqlite3',Path('/etc/harmonika-ai/unified-storage.key').read_bytes());core=Store('/var/lib/harmonika-core/core.sqlite3',Path('/etc/harmonika-ai/core-storage.key').read_bytes());count=0
with state.store._transaction() as db:
 for id in ids:
  owner=state.owner('api-token:'+str(id));assert not db.execute("SELECT 1 FROM unified_jobs WHERE actor=? AND status IN ('queued','running')",(owner,)).fetchone()
  for t in ('unified_jobs','unified_sessions'):count+=db.execute('DELETE FROM '+t+' WHERE actor=?',(owner,)).rowcount
with core._transaction() as db:
 for id in ids:
  owner=core._hash('actor','api-token:'+str(id));assert not db.execute("SELECT 1 FROM idempotency WHERE actor=? AND state='running'",(owner,)).fetchone()
  for t in ('memory','workflows','idempotency','actors'):count+=db.execute('DELETE FROM '+t+' WHERE actor=?',(owner,)).rowcount
print(json.dumps({'synthetic_actors_removed':len(ids),'encrypted_fixture_rows_removed':count}))
