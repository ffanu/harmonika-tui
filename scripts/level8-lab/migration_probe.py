"""Actual immutable v1 snapshot, native v2 migration, real lock and failure hook."""
import hashlib,json,os,shutil,sqlite3,sys,time
from pathlib import Path
sys.path.insert(0,'/app')
from workflow_engine import WorkflowStore
root=Path('/data/proofs')/('migration-'+os.environ['LEVEL8_LABEL']);root.mkdir(exist_ok=False);source=Path('/data/proofs/workflow-v1.sqlite');expected=os.environ['LEVEL8_V1_SHA256'];checks={};stores=[]
class NoChild:
 def __getattr__(self,key):raise AssertionError('clone_must_not_run_children')
def summary(path,projection=None):
 with sqlite3.connect(path) as c:
  c.row_factory=sqlite3.Row;columns={t:[x[1] for x in c.execute('PRAGMA table_info('+t+')')] for t in ['workflows','steps','events']};selected=columns if projection is None else projection
  def rows(table,order):
   fields=','.join('"'+name.replace('"','""')+'"' for name in selected[table]);return [tuple(r) for r in c.execute('SELECT '+fields+' FROM '+table+' ORDER BY '+order)]
  return {'version':c.execute('PRAGMA user_version').fetchone()[0],'columns':columns['workflows'],'projection':selected,'rows':rows('workflows','id'),'steps':rows('steps','workflow_id,name'),'events':rows('events','id'),'integrity':c.execute('PRAGMA integrity_check').fetchone()[0]}

def clone(name):
 p=root/name;shutil.copyfile(source,p);p.chmod(0o600);return p
def store(path,**kw):
 q=WorkflowStore(str(path),NoChild(),**kw);stores.append(q);return q
try:
 checks['immutable_v1_snapshot_hash']=bool(expected) and hashlib.sha256(source.read_bytes()).hexdigest()==expected
 original=summary(source);assert original['version']==1 and original['integrity']=='ok';checks['actual_v1_rows_present']=bool(original['rows'])
 target=clone('failure.sqlite');baseline=summary(target);seen=[]
 def failure(point):seen.append(point);raise RuntimeError('injected_migration_failure')
 try:store(target,schema_version=2,migration_hook=failure);checks['injected_failure_raised']=False
 except RuntimeError:checks['injected_failure_raised']=seen==['after_priority_column']
 checks['all_schema_data_version_rolled_back']=summary(target)==baseline
 store(target,schema_version=2);upgraded=summary(target,original['projection']);checks['v2_additive_columns']=upgraded['version']==2 and 'priority' in upgraded['columns'] and 'paused' in upgraded['columns'];checks['all_original_rows_steps_events_preserved']=all(upgraded[k]==original[k] for k in ['rows','steps','events'])
 with sqlite3.connect(target) as c:checks['legacy_defaults']=all(x==(0,0) for x in c.execute('SELECT priority,paused FROM workflows'))
 store(target,schema_version=2);store(target,schema_version=1);checks['repeated_init_no_downgrade']=summary(target,original['projection'])==upgraded
 locked=clone('locked.sqlite');c=sqlite3.connect(locked);c.execute('BEGIN IMMEDIATE');start=time.monotonic();raised=False
 try:
  try:store(locked,schema_version=2)
  except Exception:raised=True
 finally:c.rollback();c.close()
 checks['real_writer_lock_bounded']=raised and time.monotonic()-start<.8;checks['lock_failure_no_partial_migration']=summary(locked)==original;store(locked,schema_version=2);checks['retry_after_unlock_succeeds']=summary(locked)['version']==2
 checks['authoritative_source_untouched']=hashlib.sha256(source.read_bytes()).hexdigest()==expected
 result={'passed':all(checks.values()),'checks':checks,'workflow_engine_sha256':hashlib.sha256(Path('/app/workflow_engine.py').read_bytes()).hexdigest(),'scope':'inactive_snapshot_migration_not_live_restore'}
except Exception as e:result={'passed':False,'checks':checks,'error_type':type(e).__name__}
finally:
 for q in stores:q.stop()
print(json.dumps(result));raise SystemExit(0 if result['passed'] else 1)
