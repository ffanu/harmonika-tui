"""Protected finite SQL writer; exits naturally via own release marker/deadline."""
import json,os,sqlite3,sys,time
from pathlib import Path
kind,token=sys.argv[1:];root=Path('/data/probes');root.mkdir(exist_ok=True)
if kind not in ['writer','lock'] or not __import__('re').fullmatch('[a-f0-9]{32}',token):raise ValueError('invalid_probe')
name,table=('workflows','online_backup_probe') if kind=='writer' else ('admissions','admission_lock_probe')
path=root/(token+'.json');release=root/(token+'.release');c=sqlite3.connect('/data/'+name+'.sqlite',timeout=3,isolation_level=None)
c.execute('PRAGMA journal_mode=WAL');c.execute('CREATE TABLE IF NOT EXISTS '+table+'(id INTEGER PRIMARY KEY,value INTEGER)');c.execute('INSERT OR IGNORE INTO '+table+' VALUES(1,0)');before=c.execute('SELECT value FROM '+table+' WHERE id=1').fetchone()[0];c.execute('BEGIN IMMEDIATE');c.execute('UPDATE '+table+' SET value=value+1 WHERE id=1')
info={'state':'holding','pid':os.getpid(),'start_ticks':Path('/proc/self/stat').read_text().split()[21],'before':before,'started_at':time.time(),'kind':kind};path.write_text(json.dumps(info));end=time.monotonic()+45
while time.monotonic()<end and not release.exists():time.sleep(.025)
expired=not release.exists();c.commit();c.close();info.update(state='committed',expired=expired,finished_at=time.time(),after=before+1);path.write_text(json.dumps(info))
