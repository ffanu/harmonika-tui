"""Remove only isolated smoke data; refuses active jobs and real token principals."""
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import urllib.request
root=Path('/var/lib/harmonika-coding');actors={'api-token:900000000000000001','api-token:900000000000000002'}
report=Path('/tmp/harmonika-coding-public-smoke-result.json')
if report.is_file():
    value=json.loads(report.read_text())
    if value.get('ok') is True and type(value.get('token_ids')) is list:
        actors.update('api-token:'+str(x) for x in value['token_ids'] if type(x) is int and x>0)
with sqlite3.connect(root/'state.sqlite',timeout=10) as db:
    for a in actors:
        rows=db.execute('SELECT id,name FROM workspaces WHERE actor=?',(a,)).fetchall()
        if any(name not in ('Isolated coding live','Isolated MVP acceptance') for _,name in rows):raise SystemExit('refused_non_smoke_workspace')
        if db.execute("SELECT 1 FROM jobs WHERE actor=? AND state IN ('queued','running')",(a,)).fetchone():raise SystemExit('refused_active_smoke_job')
        for wid,_ in rows:
            folder=root/'workspaces'/hashlib.sha256(a.encode()).hexdigest()[:32]/wid
            if folder.is_symlink():raise SystemExit('refused_symlink')
            shutil.rmtree(folder,ignore_errors=True)
        db.execute('DELETE FROM events WHERE job IN (SELECT id FROM jobs WHERE actor=?)',(a,))
        db.execute('DELETE FROM jobs WHERE actor=?',(a,));db.execute('DELETE FROM turns WHERE actor=?',(a,));db.execute('DELETE FROM workspaces WHERE actor=?',(a,))
print(json.dumps({'ok':True,'isolated_principals_cleaned':len(actors)}))
