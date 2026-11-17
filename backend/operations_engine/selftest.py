"""Real isolated engine checks; no user project scripts or model success labels."""
import json,sqlite3,tempfile,subprocess,time
from pathlib import Path
from .common import fields,require

def run(config,payload):
 fields(payload,{'suite'})
 suite=payload.get('suite','core');require(suite in ('core','browser','security'),'invalid_suite')
 started=time.monotonic();checks=[]
 with tempfile.TemporaryDirectory(prefix='selftest-',dir=config['state_root']) as tmp:
  root=Path(tmp)
  if suite=='core':
   c=sqlite3.connect(root/'fixture.sqlite3')
   try:
    c.execute('PRAGMA journal_mode=WAL');c.execute('CREATE TABLE checks(id INTEGER PRIMARY KEY,value INTEGER)');c.commit()
    c.execute('INSERT INTO checks VALUES(1,42)');c.rollback();checks.append({'check':'sqlite_rollback','passed':c.execute('SELECT count(*) FROM checks').fetchone()[0]==0})
    c.execute('INSERT INTO checks VALUES(1,42)');c.commit();checks.append({'check':'sqlite_commit','passed':c.execute('SELECT value FROM checks').fetchone()[0]==42})
    checks.append({'check':'sqlite_wal','passed':c.execute('PRAGMA journal_mode').fetchone()[0]=='wal'})
    c.close()
    from .database import audit
    result=audit({'databases':{'fixture':{'type':'sqlite','path':str(root/'fixture.sqlite3'),'queries':{'one':'SELECT value FROM checks WHERE id=1'}}}},{'database':'fixture','query':'one'})
    checks.append({'check':'database_read_only_plan','passed':result['read_only'] and result['rows_returned']==0 and bool(result['query_plan'])})
    from .jobs import JobStore
    store=JobStore(root/'jobs',b'F'*32);job=store.prepare('api-token:1','fixture','selftest','fixture-one','a'*64)
    same=store.prepare('api-token:1','fixture','selftest','fixture-one','a'*64)
    checks.append({'check':'job_idempotency','passed':job['job_ref']==same['job_ref']})
   except Exception:checks.append({'check':'core_execution','passed':False})
   finally:c.close()
  else:
   project=root/'project';project.mkdir()
   fixture={'project_root':str(project),'artifacts_root':str(root/'artifacts'),'tools':config.get('tools',{}),'scanner_policy':config.get('scanner_policy',{}),'browser_policy':True}
   if suite=='browser':
    (project/'index.html').write_text('<!doctype html><title>Fixture</title><button id="ok">Ready</button>')
    from .browser import run as browser_run
    result=browser_run(fixture,{'path':'index.html','steps':[{'action':'assert_text','selector':{'by':'css','value':'#ok'},'value':'Ready'}]})
   else:
    (project/'fixture.py').write_text('def total(a,b):\n return a+b\n')
    from .scanners import scan
    result=scan(fixture,{'scanner':'semgrep','path':'.'})
   state=result.get('status');checks.append({'check':suite+'_execution','passed':state in ('completed','passed'),'status':state if state in ('completed','passed','failed','needs_toolchain','degraded') else 'failed'})
 passed=sum(x['passed'] is True for x in checks)
 return {'status':'passed' if checks and passed==len(checks) else 'partial' if any(x.get('status')=='needs_toolchain' for x in checks) else 'failed','suite':suite,'test_count':len(checks),'passed':passed,'failed':len(checks)-passed,'checks':checks,'latency_ms':round((time.monotonic()-started)*1000,2),'execution_scope':'isolated_synthetic_fixture','all_catalog_skills_certified':False}
