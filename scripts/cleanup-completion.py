#!/usr/bin/env python3
"""Root-only exact-actor synthetic cleanup for the isolated acceptance harness."""
import json,os,sqlite3,sys,time,urllib.request
from pathlib import Path
stage=os.environ.get('HARMONIKA_COMPLETION_STAGE')=='1';base=Path('/opt/harmonika-ai/unified-completion-stage') if stage else Path('/opt/harmonika-ai')
sys.path.insert(0,str(base/'backend') if stage else str(base))
from unified_engine.state import State
ids=json.load(sys.stdin)['actor_ids'];assert type(ids) is list and len(ids)<=4 and all(type(i) is int and i>0 for i in ids)
dbpath=base/'private/state.sqlite3' if stage else Path('/var/lib/harmonika-unified/state.sqlite3');state=State(dbpath,Path('/etc/harmonika-ai/unified-storage.key').read_bytes());secret=Path('/etc/harmonika-ai/unified-engine.token').read_text().strip();count=0
for id in ids:
 actor='api-token:'+str(id);owner=state.owner(actor)
 with state.store._transaction() as db:jobs=db.execute("SELECT id FROM unified_jobs WHERE actor=? AND status IN ('queued','running')",(owner,)).fetchall()
 for row in jobs:
  req=urllib.request.Request('http://127.0.0.1:'+('18402' if stage else '18196')+'/jobs/'+row['id']+'/stop',method='POST',data=b'{}',headers={'X-Harmonika-Unified-Secret':secret,'X-Harmonika-Principal':actor,'Content-Type':'application/json'})
  with urllib.request.urlopen(req,timeout=10) as r:assert r.status==200
 if jobs:time.sleep(.2)
 with state.store._transaction() as db:
  assert not db.execute("SELECT 1 FROM unified_jobs WHERE actor=? AND status IN ('queued','running')",(owner,)).fetchone()
  tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
  for table in ('unified_jobs','unified_sessions','unified_ext_events','unified_ext_topics','unified_ext_secrets','unified_ext_secret_ops'):
   if table in tables:count+=db.execute('DELETE FROM '+table+' WHERE actor=?',(owner,)).rowcount
print(json.dumps({'synthetic_actors_cleaned':len(ids),'encrypted_fixture_rows_removed':count,'stage':stage}))
