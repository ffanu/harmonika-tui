"""Online WAL snapshot under a real write lock, separate restore clone only."""
import hashlib,json,os,sqlite3,stat,sys,time,urllib.request,urllib.error
from pathlib import Path
sys.path.insert(0,'/app');root=Path('/data')/('backup-'+os.environ['LEVEL7_LABEL']);checks={};reader=None;writer=None
def req(path,body=None,key=None):
 headers={'X-Lab-Owner':'alpha','Content-Type':'application/json'}
 if key:headers['Idempotency-Key']=key
 q=urllib.request.Request('http://127.0.0.1:18081'+path,headers=headers,data=json.dumps(body).encode() if body is not None else None,method='POST' if body is not None else 'GET')
 try:r=urllib.request.urlopen(q,timeout=3)
 except urllib.error.HTTPError as e:r=e
 with r:return r.status,json.loads(r.read())
def rejected(fn):
 try:fn()
 except (OSError,ValueError,sqlite3.DatabaseError):return True
 return False
try:
 from storage_tools import backup,restore
 root.mkdir(mode=0o700,exist_ok=False);src=Path('/data/jobs.sqlite');dest=root/'snapshot.sqlite';clone=root/'recovery.sqlite'
 reader=sqlite3.connect(src);reader.execute('BEGIN');reader.execute('SELECT count(*) FROM jobs').fetchone()
 status,value=req('/jobs',{'kind':'report','text':'WAL snapshot marker'},'backup-'+os.environ['LEVEL7_LABEL']);assert status==202;id=value['job']['id']
 for _ in range(30):
  status,value=req('/jobs/'+id)
  if value['job']['state']=='completed':break
  time.sleep(.1)
 assert value['job']['state']=='completed'
 writer=sqlite3.connect(src);writer.execute('BEGIN IMMEDIATE');writer.execute('UPDATE admission SET draining=draining WHERE id=1')
 checks['wal_pages_exist_with_live_reader']=Path(str(src)+'-wal').exists() and Path(str(src)+'-wal').stat().st_size>32
 manifest=backup(str(src),str(dest));checks['backup_during_active_writer']=writer.in_transaction
 writer.rollback();writer.close();writer=None;reader.rollback();reader.close();reader=None
 checks['actual_snapshot_hash']=manifest['sha256']==hashlib.sha256(dest.read_bytes()).hexdigest() and manifest['bytes']==dest.stat().st_size
 checks['snapshot_0600_no_sidecars']=stat.S_IMODE(dest.stat().st_mode)==0o600 and not any(Path(str(dest)+x).exists() for x in ['-wal','-shm','-journal'])
 restore(str(dest),str(clone),manifest['sha256'])
 with sqlite3.connect('file:'+str(clone)+'?mode=ro',uri=True) as c:
  checks['restored_integrity_delete_mode']=c.execute('PRAGMA integrity_check').fetchone()[0]=='ok' and c.execute('PRAGMA journal_mode').fetchone()[0]=='delete'
  checks['wal_only_marker_preserved']=c.execute('SELECT state FROM jobs WHERE id=?',(id,)).fetchone()[0]=='completed'
  checks['manifest_from_snapshot']=manifest['job_count']==c.execute('SELECT count(*) FROM jobs').fetchone()[0] and manifest['event_count']==c.execute('SELECT count(*) FROM events').fetchone()[0] and manifest['last_event_id']==c.execute('SELECT COALESCE(MAX(id),0) FROM events').fetchone()[0]
 checks['restored_0600_hash_matches']=stat.S_IMODE(clone.stat().st_mode)==0o600 and hashlib.sha256(clone.read_bytes()).hexdigest()==manifest['sha256']
 bad=root/'wrong-hash.sqlite';checks['wrong_hash_no_destination']=rejected(lambda:restore(str(dest),str(bad),'0'*64)) and not bad.exists()
 corrupt=root/'corrupt.sqlite';corrupt.write_bytes(b'NOTSQLITE'+dest.read_bytes()[9:]);bad2=root/'corrupt-restore.sqlite';checks['corrupt_integrity_rejected']=rejected(lambda:restore(str(corrupt),str(bad2),hashlib.sha256(corrupt.read_bytes()).hexdigest())) and not bad2.exists()
 existing=root/'user.sqlite';existing.write_bytes(b'USER_KEEP');checks['existing_destination_preserved']=rejected(lambda:restore(str(dest),str(existing),manifest['sha256'])) and existing.read_bytes()==b'USER_KEEP'
 link=root/'link.sqlite';link.symlink_to(dest);bad3=root/'link-restore.sqlite';checks['source_symlink_refused']=rejected(lambda:restore(str(link),str(bad3),manifest['sha256'])) and not bad3.exists()
 destlink=root/'destlink.sqlite';destlink.symlink_to(existing);checks['destination_symlink_refused']=rejected(lambda:restore(str(dest),str(destlink),manifest['sha256'])) and existing.read_bytes()==b'USER_KEEP'
 with sqlite3.connect(src) as c:checks['authoritative_source_stays_wal']=c.execute('PRAGMA journal_mode').fetchone()[0]=='wal' and c.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
 checks['no_temp_sidecars']=not any(p.name.endswith(('-wal','-shm','-journal','.tmp')) for p in root.iterdir())
 print(json.dumps({'passed':all(checks.values()),'checks':checks,'manifest':manifest,'storage_tools_sha256':hashlib.sha256(Path('/app/storage_tools.py').read_bytes()).hexdigest(),'scope':'point_in_time_isolated_clone_not_live_cluster_restore'}))
except Exception as e:print(json.dumps({'passed':False,'checks':checks,'error_type':type(e).__name__}));raise SystemExit(1)
finally:
 for c in [writer,reader]:
  if c is not None:
   try:c.rollback();c.close()
   except sqlite3.Error:pass
