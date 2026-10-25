#!/usr/bin/env python3
"""Root-only cleanup of actors minted by the isolated shared-core acceptance test."""
import hashlib,json,re,shutil,sqlite3,sys
from pathlib import Path
sys.path.insert(0,'/opt/harmonika-ai')
from core_engine.state import Store
ids=json.load(sys.stdin)['actor_ids'];assert type(ids) is list and len(ids)<=4 and all(type(i) is int and i>0 for i in ids)
actors=['api-token:'+str(i) for i in ids]
root=Path('/var/lib/harmonika-coding');folders=[];count=0
with sqlite3.connect(root/'state.sqlite',timeout=10) as db:
 db.execute('BEGIN IMMEDIATE')
 for actor in actors:
  rows=db.execute('SELECT id,name FROM workspaces WHERE actor=?',(actor,)).fetchall();assert all(name=='SYNTHETIC CORE FOUNDATION' for _,name in rows)
  assert not db.execute("SELECT 1 FROM jobs WHERE actor=? AND state IN ('queued','running','approval_required')",(actor,)).fetchone(),'active_fixture_job'
  for wid,_ in rows:
   folder=root/'workspaces'/hashlib.sha256(actor.encode()).hexdigest()[:32]/wid
   assert not folder.is_symlink() and folder.resolve().parent==folder.parent.resolve() and root.resolve() in folder.resolve().parents
   folders.append(folder)
  db.execute('DELETE FROM events WHERE job IN (SELECT id FROM jobs WHERE actor=?)',(actor,))
  for table in ('jobs','turns','workspaces'):db.execute('DELETE FROM '+table+' WHERE actor=?',(actor,))
for folder in folders:
 if folder.exists():shutil.rmtree(folder)
store=Store('/var/lib/harmonika-core/core.sqlite3',Path('/etc/harmonika-ai/core-storage.key').read_bytes())
with store._transaction() as db:
 for actor in actors:
  owner=store._hash('actor',actor)
  assert not db.execute("SELECT 1 FROM idempotency WHERE actor=? AND state='running'",(owner,)).fetchone(),'active_fixture_workflow'
  for table in ('memory','workflows','idempotency','actors'):count+=db.execute('DELETE FROM '+table+' WHERE actor=?',(owner,)).rowcount
print(json.dumps({'fixture_actors_cleaned':len(actors),'fixture_workspaces_removed':len(folders),'encrypted_fixture_rows_removed':count}))
