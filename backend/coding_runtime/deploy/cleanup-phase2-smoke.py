"""Owner-only removal of successful isolated Phase2 production fixtures."""
import hashlib,json,shutil,sqlite3
from pathlib import Path
root=Path('/var/lib/harmonika-coding');actors=set()
for name in ('/tmp/harmonika-phase2-production-smoke-first.json','/tmp/harmonika-phase2-production-smoke-result.json'):
    p=Path(name)
    if p.is_file():
        r=json.loads(p.read_text());assert r['ok'] is True
        actors.update(r['actors'])
folders=[]
with sqlite3.connect(root/'state.sqlite',timeout=10) as d:
    d.execute('BEGIN IMMEDIATE')
    for a in sorted(actors):
        assert a.startswith('api-token:') and a[10:].isdigit()
        rows=d.execute('SELECT id,name FROM workspaces WHERE actor=?',(a,)).fetchall()
        assert all(name=='Isolated Phase2 acceptance' for _,name in rows),'non_fixture_workspace_refused'
        assert not d.execute("SELECT 1 FROM jobs WHERE actor=? AND state IN ('queued','running')",(a,)).fetchone(),'active_test_job_refused'
        assert not d.execute("SELECT 1 FROM debug_sessions WHERE actor=? AND state NOT IN ('closed','expired','interrupted','failed')",(a,)).fetchone(),'active_debug_refused'
        for wid,_ in rows:
            folder=root/'workspaces'/hashlib.sha256(a.encode()).hexdigest()[:32]/wid
            assert not folder.is_symlink() and root.resolve() in folder.resolve().parents
            folders.append(folder)
        d.execute('DELETE FROM debug_actions WHERE session IN (SELECT id FROM debug_sessions WHERE actor=?)',(a,))
        d.execute('DELETE FROM debug_events WHERE session IN (SELECT id FROM debug_sessions WHERE actor=?)',(a,))
        d.execute('DELETE FROM debug_sessions WHERE actor=?',(a,))
        d.execute('DELETE FROM events WHERE job IN (SELECT id FROM jobs WHERE actor=?)',(a,))
        for table in ('jobs','turns','workspaces'):d.execute('DELETE FROM '+table+' WHERE actor=?',(a,))
for folder in folders:shutil.rmtree(folder)
print(json.dumps({'ok':True,'isolated_actors_cleaned':len(actors),'isolated_workspaces_removed':len(folders)}))
