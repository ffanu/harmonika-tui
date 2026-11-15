"""Actual dedicated tmpfs ENOSPC/SQLite failure; never fills host or live volume."""
import errno,json,os,sqlite3,time
from pathlib import Path
from workflow_engine import WorkflowStore
from recovery_engine import RecoveryManager
from workflow_adapter import ChildClient
root=Path('/faultdisk')
assert os.statvfs(root).f_blocks*os.statvfs(root).f_frsize<=2097152
assert not list(root.iterdir()),'probe_must_be_fresh'
w=WorkflowStore(root/'workflows.sqlite',ChildClient(),schema_version=4);m=RecoveryManager(w,root);c=sqlite3.connect(root/'admissions.sqlite',timeout=.15,isolation_level=None)
c.execute('PRAGMA wal_autocheckpoint=0');c.execute('CREATE TABLE ballast(id INTEGER PRIMARY KEY,value BLOB)')
free_before=os.statvfs(root).f_bavail*os.statvfs(root).f_frsize
try:
 with (root/'space.pad').open('wb') as f:
  for i in range(2048):f.write(b'0'*4096);f.flush()
except OSError as e:
 assert e.errno==errno.ENOSPC
else:raise RuntimeError('actual_tmpfs_full_not_reached')
try:c.execute('INSERT INTO ballast(value) VALUES(?)',(b'x'*65536,));raise RuntimeError('actual_sqlite_full_not_reached')
except sqlite3.Error as e:code=getattr(e,'sqlite_errorcode',None)
payload={'text':'storage synthetic','tasks':[{'name':'prepare','needs':[]},{'name':'publish','needs':['prepare']}]}
free_full=os.statvfs(root).f_bavail*os.statvfs(root).f_frsize
try:status,value=m.submit('alpha','storage-retry',payload);exception=False
except Exception:status,value,exception=0,{},True
# Free ONLY our pad/ballast, retain journal intents/workflow IDs for same-key retry.
(root/'space.pad').unlink(missing_ok=True);c.execute('DROP TABLE ballast');c.execute('PRAGMA wal_checkpoint(TRUNCATE)');c.close()
status_after,value_after=m.submit('alpha','storage-retry',payload)
print(json.dumps({'sqlite_error_code':code,'tmpfs_bytes':os.statvfs(root).f_blocks*os.statvfs(root).f_frsize,'free_bytes_before':free_before,'free_bytes_at_failure':free_full,'failure_status':status,'failure_error':value.get('error'),'exception_escaped':exception,'recovered_status':status_after,'recovered_workflow_id':value_after.get('workflow',{}).get('id'),'live_data_modified':False,'fixture_only':True}))
