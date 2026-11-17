"""Registered read-only database audit; never returns customer rows or SQL text."""
import json,os,re,sqlite3,subprocess,tempfile
from contextlib import closing
from pathlib import Path
from .common import OpsError,require,fields,private_path
IDENT=re.compile(r'^[A-Za-z_][A-Za-z0-9_]{0,62}$')

def postgres(config,sql):
 service=config.get('service');require(type(service) is str and IDENT.fullmatch(service),'invalid_database_connector',503)
 sf=private_path(config['service_file']);exe=Path(config.get('psql','/usr/bin/psql'))
 require(exe.is_absolute() and exe.is_file() and not exe.is_symlink(),'database_toolchain_missing',503)
 env={k:v for k,v in os.environ.items() if k in ('PATH','LANG','LC_ALL','SYSTEMROOT','WINDIR')}
 env.update(PGSERVICEFILE=str(sf),PGOPTIONS='-c default_transaction_read_only=on -c statement_timeout=3000 -c lock_timeout=500',PGCONNECT_TIMEOUT='3')
 with tempfile.TemporaryFile() as out,tempfile.TemporaryFile() as err:
  try:r=subprocess.run([str(exe),'-X','-A','-t','-v','ON_ERROR_STOP=1','--dbname','service='+service],input=('BEGIN READ ONLY;\n'+sql+'\nCOMMIT;\n').encode(),stdout=out,stderr=err,env=env,timeout=8)
  except (OSError,subprocess.TimeoutExpired):raise OpsError('database_unavailable',503) from None
  require(r.returncode==0,'database_query_failed',502);out.seek(0);raw=out.read(262145);require(len(raw)<=262144,'database_result_limit',502)
 # -q prevents command tags on older psql; tolerate only exact protocol tags.
 rows=[x for x in raw.decode('utf-8').splitlines() if x.strip() and x not in ('BEGIN','COMMIT')]
 require(len(rows)==1,'invalid_database_response',502)
 try:return json.loads(rows[0])
 except ValueError:raise OpsError('invalid_database_response',502) from None

def audit(config,payload):
 fields(payload,{'database','query'},('database',))
 aliases=config.get('databases',{});target=aliases.get(payload['database'])
 require(type(target) is dict,'database_not_registered',404)
 query_id=payload.get('query');query=None
 if query_id is not None:
  require(type(query_id) is str and query_id in target.get('queries',{}),'query_not_registered',404)
  query=target['queries'][query_id]
  require(type(query) is str and len(query)<=8192 and re.match(r'^\s*SELECT\s',query,re.I) and ';' not in query,'unsafe_registered_query',503)
 if target.get('type')=='postgres':
  value=postgres(target,"SELECT json_build_object('tables',(SELECT count(*) FROM information_schema.tables WHERE table_schema='public'),'indexes',(SELECT count(*) FROM pg_indexes WHERE schemaname='public'),'read_only',current_setting('transaction_read_only')='on');")
  require(value.get('read_only') is True,'database_not_read_only',502)
  if query:
   raw=postgres(target,'EXPLAIN (FORMAT JSON) '+query);nodes=[];pending=[raw[0]['Plan']]
   while pending:
    p=pending.pop();nodes.append({'node_type':p.get('Node Type'),'estimated_rows':p.get('Plan Rows'),'uses_index':'Index' in p.get('Node Type','')});pending.extend(p.get('Plans',[]))
   value['query_plan']=nodes[:128]
  return {'status':'completed','engine':'postgres','read_only':True,'schema':value,'rows_returned':0,'execution_scope':'registered_database'}
 require(target.get('type')=='sqlite','unsupported_database',503)
 p=Path(target.get('path','')).absolute();require(p.is_file() and not any(x.is_symlink() for x in [p,*p.parents]),'unsafe_database_path')
 require(p.stat().st_nlink==1 and p.stat().st_size<=2*1024**3,'database_too_large')
 try:
  with closing(sqlite3.connect(p.as_uri()+'?mode=ro',uri=True,timeout=.5)) as c:
   c.execute('PRAGMA query_only=ON');c.execute('PRAGMA trusted_schema=OFF')
   budget=[0]
   def progress():budget[0]+=1;return int(budget[0]>2000)
   c.set_progress_handler(progress,1000)
   allowed={sqlite3.SQLITE_SELECT,sqlite3.SQLITE_READ,sqlite3.SQLITE_FUNCTION,sqlite3.SQLITE_RECURSIVE}
   def authorizer(action,a,b,db,source):
    if action==sqlite3.SQLITE_PRAGMA and a in {'table_info','index_list','quick_check','foreign_key_check','query_only'}:return sqlite3.SQLITE_OK
    if action==sqlite3.SQLITE_FUNCTION and (b or a or '').lower() in {'load_extension','writefile','readfile'}:return sqlite3.SQLITE_DENY
    return sqlite3.SQLITE_OK if action in allowed else sqlite3.SQLITE_DENY
   c.set_authorizer(authorizer)
   tables=c.execute("SELECT name FROM sqlite_schema WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name LIMIT 129").fetchall();require(len(tables)<=128,'database_schema_limit')
   schema=[];partial=False
   for (name,) in tables:
    require(IDENT.fullmatch(name),'unsupported_schema_identifier')
    columns=c.execute('PRAGMA table_info("'+name+'")').fetchall();indexes=c.execute('PRAGMA index_list("'+name+'")').fetchall()
    partial=partial or len(columns)>128
    schema.append({'table':name,'columns_total':len(columns),'columns_truncated':len(columns)>128,'columns':[{'name':x[1],'type':x[2],'not_null':bool(x[3]),'primary_key':bool(x[5])} for x in columns[:128]],'index_count':len(indexes)})
   healthy=c.execute('PRAGMA quick_check(1)').fetchone()==('ok',)
   result={'status':'partial' if partial else 'completed','schema_complete':not partial,'engine':'sqlite','read_only':True,'integrity_ok':healthy,'schema':schema,'rows_returned':0,'execution_scope':'registered_database'}
   if query:result['query_plan']=[{'step':r[0],'parent':r[1],'detail':r[3][:256]} for r in c.execute('EXPLAIN QUERY PLAN '+query).fetchmany(129)][:128]
   return result
 except sqlite3.Error:raise OpsError('database_audit_failed',502) from None
