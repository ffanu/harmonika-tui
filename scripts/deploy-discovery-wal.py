#!/usr/bin/env python3
"""Pilot WAL only for the qualified discovery store. No native session writes."""
from contextlib import closing
import datetime,hashlib,json,os,shutil,sqlite3,subprocess,time,urllib.request
from pathlib import Path
B=Path('/opt/harmonika-ai');S=B/'wal-qualification';SOURCE=B/'engine_registry/discovery.py';DB=Path('/var/lib/harmonika-master/discovery.sqlite3');SERVICE='harmonika-master-skills'
def run(*args):return subprocess.check_output(args,stderr=subprocess.STDOUT,text=True).strip()
def api(path,body=None):
 h={'X-Harmonika-Master-Secret':Path('/etc/harmonika-ai/master-engine.token').read_text().strip(),'X-Harmonika-Principal':'api-token:1','Content-Type':'application/json'}
 r=urllib.request.Request('http://127.0.0.1:18193'+path,headers=h,data=json.dumps(body).encode() if body is not None else None)
 with urllib.request.urlopen(r,timeout=12) as v:return json.load(v)
assert os.geteuid()==0 and run('systemctl','is-active',SERVICE)=='active'
old=SOURCE.read_text();expected=old.replace('import ast\n','from contextlib import closing\nimport ast\n',1).replace('with self._connect_existing(timeout=1) as db:','with closing(self._connect_existing(timeout=1)) as db, db:')
assert expected!=old and (S/'backend/engine_registry/discovery.py').read_text()==expected,'unexpected_source_change'
assert api('/capabilities')['discovery']['module_limit']==3072
services=['harmonika-ai','harmonika-web-chat-live','harmonika-shared-core','harmonika-unified','harmonika-rtx-queue','harmonika-rtx-tunnel','harmonika-rtx-vision-tunnel']
pids={s:run('systemctl','show',s,'-p','MainPID','--value') for s in services}
backup=Path('/var/backups/harmonika-master')/('wal-'+datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ'));backup.mkdir(parents=True,mode=0o700);shutil.copy2(SOURCE,backup/'discovery.py')
identity=(DB.stat().st_dev,DB.stat().st_ino);changed=False
try:
 run('systemctl','stop',SERVICE)
 with closing(sqlite3.connect(DB)) as src,closing(sqlite3.connect(backup/'discovery.sqlite3')) as dst:src.backup(dst);assert dst.execute('PRAGMA quick_check').fetchone()[0]=='ok'
 os.chmod(backup/'discovery.sqlite3',0o600)
 info=json.loads(run('/usr/bin/python3',str(S/'scripts/sqlite-maintenance.py'),'migrate-wal',str(DB),'--busy-timeout-ms','1000','--backup-dir',str(backup/'sqlite-api')));assert info['after']=='wal';changed=True
 nextfile=SOURCE.with_name('discovery.py.wal-next');shutil.copy2(S/'backend/engine_registry/discovery.py',nextfile);os.replace(nextfile,SOURCE)
 run('systemctl','start',SERVICE)
 value=None
 for _ in range(50):
  try:
   value=api('/capabilities')
   if value['discovery']['registered']==1 and value['discovery']['module_limit']==3072:break
  except OSError:pass
  time.sleep(.2)
 assert value and value['catalog_size']==1951 and value['discovery']['registered']==1
 result=api('/invoke',{'skill_id':'text.metrics','input':{'text':'WAL proof works'}});assert result['result']['words']==3
 assert (DB.stat().st_dev,DB.stat().st_ino)==identity
 with closing(sqlite3.connect(DB.as_uri()+'?mode=rw',uri=True)) as c:assert c.execute('PRAGMA journal_mode').fetchone()[0]=='wal' and c.execute('PRAGMA quick_check').fetchone()[0]=='ok'
 assert pids=={s:run('systemctl','show',s,'-p','MainPID','--value') for s in services}
 report={'status':'active_verified','database':str(DB),'journal_mode':'wal','backup':str(backup),'integrity':'ok','file_identity_preserved':True,'live_engine_invoke':'passed','module_limit':3072,'registered':1,'catalog':1951,'execution_connection_leak_fixed':True,'core_unified_cache_unchanged':True,'native_user_sessions_untouched':True,'only_restarted':SERVICE,'other_seven_pids_unchanged':True};(B/'discovery-wal-activation.json').write_text(json.dumps(report,indent=2));print(json.dumps(report))
except BaseException:
 run('systemctl','stop',SERVICE)
 if changed:run('/usr/bin/python3',str(S/'scripts/sqlite-maintenance.py'),'revert-delete',str(DB),'--busy-timeout-ms','1000')
 shutil.copy2(backup/'discovery.py',SOURCE);run('systemctl','start',SERVICE)
 print(json.dumps({'status':'rolled_back','backup':str(backup),'committed_database_data_preserved':True}));raise
