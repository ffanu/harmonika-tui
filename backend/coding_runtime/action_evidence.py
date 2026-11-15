"""Server-owned action ledger. No caller history, model phase or stdout grants proof.

The authenticated coding gateway supplies actor; workspace identity comes from the
existing runtime. Workers receive a bounded COW snapshot, never this DB/key.
"""
import ast,asyncio,base64,hashlib,hmac,json,os,re,secrets,sqlite3,time
from uuid import uuid4
from .core import CodingError,actor,identifier,fingerprint

PROFILES={'observed_steps','read','artifact','tests','migration','conversation','plan_only'}
MAX_AGE=300

def digest(blob):return hashlib.sha256(blob).hexdigest()
def path_id(name):return digest(name.encode())
def bytes_map(snapshot):return {n:base64.b64decode(b,validate=True) for n,b in snapshot.items()}
def revision(snapshot):return fingerprint({n:digest(b) for n,b in bytes_map(snapshot).items()})
def safe_paths(value):
    from .sandbox import validate_rel_path
    if type(value) is not list or not 1<=len(value)<=8 or any(type(p) is not str for p in value) or len(set(value))!=len(value):raise CodingError('invalid_verification_paths')
    for p in value:validate_rel_path(p)
    return value

# Attestation eligibility is deliberately narrower than execution support. Docker
# remains the OS sandbox. Reflective/metaprogramming projects still run after
# approval, but cannot claim independent test proof through this bounded profile.
def supported_python(snapshot):
    import unittest,json,sqlite3,decimal,math
    protected_members=set().union(*(set(c.__dict__) for c in (unittest.TestCase,unittest.TestResult,unittest.TestSuite,unittest.TestLoader)))
    for module in (unittest,json,sqlite3,decimal,math):
        protected_members.update(vars(module))
        for obj in vars(module).values():
            if isinstance(obj,type):protected_members.update(vars(obj))
    blobs=bytes_map(snapshot);local={n[:-3].replace('/','.') for n in blobs if n.endswith('.py')}
    allowed={'unittest','sqlite3','decimal','math','json'}|local
    forbidden={'eval','exec','compile','globals','locals','vars','dir','getattr','setattr','delattr','hasattr','__import__','type','help','input','breakpoint','exit','quit','SystemExit'}
    attrs={'sys','os','inspect','builtins','subprocess','importlib','ctypes','marshal','pickle','traceback','f_back','f_code','f_locals','f_globals','co_consts','gi_frame','cr_frame','ag_frame','tb_frame','func_globals','with_traceback','format','format_map','mock','patch','open','read_text','read_bytes','write_text','write_bytes','load_extension','enable_load_extension','Connection','dbapi2','set_authorizer'}
    for name,blob in blobs.items():
        # Native modules / cached bytecode could shadow an admitted .py module
        # and bypass its AST check. Those projects require another verifier.
        if name.lower().endswith(('.so','.pyd','.dll','.dylib','.pyc','.pyo','.pth','.zip','.egg','.whl')):return False
        if not name.endswith('.py'):continue
        root=name.split('/')[0];module=root[:-3] if root.endswith('.py') else root
        if module in (set(getattr(__import__('sys'),'stdlib_module_names',set()))|{'unittest','json','sqlite3','decimal','math','sys','os','io','contextlib','hashlib'}):return False
        try:tree=ast.parse(blob.decode('utf-8'));parents={child:n for n in ast.walk(tree) for child in ast.iter_child_nodes(n)}
        except (SyntaxError,UnicodeError,ValueError,RecursionError):return False
        for n in ast.walk(tree):
            if isinstance(n,(ast.Import,ast.ImportFrom)):
                modules=[a.name for a in n.names] if isinstance(n,ast.Import) else [n.module]
                if getattr(n,'level',0) or any(m not in allowed for m in modules):return False
                exports={'unittest':{'TestCase','IsolatedAsyncioTestCase'},'json':{'loads','dumps','JSONDecodeError'},'decimal':{'Decimal','ROUND_HALF_UP','ROUND_HALF_EVEN','ROUND_DOWN','ROUND_UP'},'sqlite3':set()}
                if isinstance(n,ast.ImportFrom) and n.module in exports and any(a.name not in exports[n.module] for a in n.names):return False
                if any(a.name.startswith('_') or (a.asname or '') in forbidden|attrs for a in n.names):return False
            if isinstance(n,ast.Name) and (n.id in forbidden or n.id.startswith('__') and n.id!='__name__'):return False
            if isinstance(n,ast.Name) and n.id=='self' and isinstance(n.ctx,(ast.Store,ast.Del)):return False
            if isinstance(n,ast.Attribute) and (n.attr.startswith('_') or n.attr in attrs):return False
            # Temporary monkeypatch + restoration could evade end-of-run identity
            # checks. Only ordinary instance fields can be assigned; framework
            # members cannot be replaced even through an alias named self.
            if isinstance(n,ast.Attribute) and isinstance(n.ctx,(ast.Store,ast.Del)):
                if not isinstance(n.value,ast.Name) or n.value.id!='self' or n.attr in protected_members:return False
            if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)) and n.name.startswith('__') and n.name!='__init__':return False
            if isinstance(n,ast.Constant) and isinstance(n.value,str) and '__' in n.value and n.value!='__main__':return False
            # File/SQLite capabilities cannot be aliased or point at /proc/host
            # paths to steal the private per-run framing challenge from argv.
            is_open=isinstance(n,ast.Name) and n.id=='open'
            is_connect=isinstance(n,ast.Attribute) and n.attr=='connect'
            if is_open or is_connect:
                call=parents.get(n)
                if not isinstance(call,ast.Call) or call.func is not n or not call.args or not isinstance(call.args[0],ast.Constant) or not isinstance(call.args[0].value,str):return False
                target=call.args[0].value
                if target==':memory:' and is_connect:continue
                try:
                    from .sandbox import validate_rel_path
                    validate_rel_path(target)
                except Exception:return False
                if target.startswith('file:') or '?' in target or '#' in target:return False
    return True

# A fresh isolated verifier runs the unchanged admitted assertions. Source and test
# output is captured and discarded; only mechanics cross back to the issuer.
# This is a trusted-test/framework assumption, not an adversarial Python VM proof.
def test_program(paths):
    return '''import sys
sys.path=[p for p in sys.path if p.startswith(sys.base_prefix+'/lib/python') and not any(x in p for x in ('site-packages','dist-packages'))]
import contextlib,io,json,os,unittest,sqlite3,decimal,math,asyncio
original_connect=sqlite3.connect
def verified_connect(*args,**kwargs):
 connection=original_connect(*args,**kwargs)
 connection.set_authorizer(lambda action,*rest:sqlite3.SQLITE_DENY if action in (sqlite3.SQLITE_ATTACH,sqlite3.SQLITE_DETACH) else sqlite3.SQLITE_OK)
 return connection
sqlite3.connect=verified_connect
sys.path.insert(0,os.getcwd())
paths='''+repr(paths)+'''
classes=[unittest.TestCase,unittest.TestResult,unittest.TestLoader,unittest.TestSuite]
bindings={c:{k:(v,getattr(v,'__code__',None)) for k,v in c.__dict__.items()} for c in classes}
def unchanged():
 return all(set(c.__dict__)==set(saved) and all(c.__dict__[k] is v and getattr(v,'__code__',None) is code for k,(v,code) in saved.items()) for c,saved in bindings.items())
loader=unittest.TestLoader();suite=unittest.TestSuite();result=unittest.TestResult()
encode=json.JSONEncoder(separators=(',',':')).encode
capture=io.StringIO()
with contextlib.redirect_stdout(capture),contextlib.redirect_stderr(capture):
 for path in paths:
  module=path[:-3].replace('/', '.')
  suite.addTests(loader.loadTestsFromName(module))
 if not unchanged(): raise RuntimeError('verification_framework_changed')
 suite.run(result)
 if not unchanged(): raise RuntimeError('verification_framework_changed')
value={'collected':result.testsRun,'passed':result.testsRun-len(result.failures)-len(result.errors)-len(result.skipped)-len(result.expectedFailures)-len(result.unexpectedSuccesses),'failed':len(result.failures)+len(result.errors)+len(result.unexpectedSuccesses),'skipped':len(result.skipped)+len(result.expectedFailures),'awaited':True}
value['_verification_nonce']='__HARMONIKA_VERIFIER_NONCE__'
print(encode(value))
sys.exit(0 if result.wasSuccessful() and value['passed']==value['collected'] and value['collected']>0 else 3)
'''

def migration_program(path,expected,tables):
    return '''import sys
sys.path=[p for p in sys.path if p.startswith(sys.base_prefix+'/lib/python') and not any(x in p for x in ('site-packages','dist-packages'))]
import hashlib,json,sqlite3
from urllib.parse import quote
p='''+repr(path)+'''
expected='''+repr(tables)+'''
d=sqlite3.connect('file:'+quote(p,safe='/')+'?mode=ro',uri=True)
v=d.execute('PRAGMA user_version').fetchone()[0];integrity=d.execute('PRAGMA integrity_check').fetchone()[0]
names={x[0] for x in d.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")};valid=True
for name,rule in expected.items():
 if name not in names:valid=False;continue
 actual={x[1]:x[2].upper() for x in d.execute('PRAGMA table_info("'+name+'")')}
 if actual!=rule['columns']:valid=False;continue
 count=d.execute('SELECT count(*) FROM "'+name+'"').fetchone()[0]
 if count<rule['minimum_rows']:valid=False
 if rule.get('row_digest'):
  if count>10000:valid=False;continue
  cols=','.join('"'+c+'"' for c in sorted(rule['columns']));rows=d.execute('SELECT '+cols+' FROM "'+name+'" ORDER BY '+cols).fetchall()
  def scalar(value):return {'blob':value.hex()} if isinstance(value,bytes) else value
  content=json.dumps([[scalar(v) for v in row] for row in rows],ensure_ascii=False,separators=(',',':'),allow_nan=False).encode()
  if hashlib.sha256(content).hexdigest()!=rule['row_digest']:valid=False
print(json.dumps({'schema':v,'integrity':integrity,'tables_ok':valid,'_verification_nonce':'__HARMONIKA_VERIFIER_NONCE__'},separators=(',',':')))
d.close()
'''

class ActionEvidence:
    def __init__(self,runtime,clock=time.time):
        self.runtime=runtime;self.clock=clock;self._verifier_slots=asyncio.Semaphore(1)
        p=runtime.root/'.action-issuer.key'
        try:
            fd=os.open(p,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
            try:os.write(fd,os.urandom(32));os.fsync(fd)
            finally:os.close(fd)
        except FileExistsError:pass
        fd=os.open(p,os.O_RDONLY|os.O_NOFOLLOW)
        try:
            st=os.fstat(fd)
            if st.st_uid!=os.getuid() or st.st_mode&0o077 or st.st_size!=32:raise CodingError('unsafe_proof_key',503)
            self._key=os.read(fd,33)
        finally:os.close(fd)
        with runtime.store.db() as d:
            d.executescript('''
CREATE TABLE IF NOT EXISTS action_tasks(id TEXT PRIMARY KEY,actor TEXT NOT NULL,workspace TEXT NOT NULL,profile TEXT NOT NULL,config TEXT NOT NULL,state TEXT NOT NULL,created REAL NOT NULL,expires REAL NOT NULL,revision TEXT,version INTEGER,checks TEXT NOT NULL DEFAULT '{}',verified_at REAL,mac TEXT,envelope TEXT);
CREATE UNIQUE INDEX IF NOT EXISTS action_active_scope ON action_tasks(actor,workspace) WHERE state='active';
CREATE TABLE IF NOT EXISTS action_turns(actor TEXT NOT NULL,workspace TEXT NOT NULL,turn_key TEXT NOT NULL,task TEXT NOT NULL,digest TEXT NOT NULL,PRIMARY KEY(actor,turn_key));
CREATE TABLE IF NOT EXISTS action_operations(task TEXT NOT NULL,step_key TEXT NOT NULL,input_hash TEXT NOT NULL,skill TEXT NOT NULL,job TEXT,created REAL NOT NULL,outcome TEXT NOT NULL DEFAULT 'intent',PRIMARY KEY(task,step_key));
''')
            if 'envelope' not in {x[1] for x in d.execute('PRAGMA table_info(action_tasks)')}:d.execute('ALTER TABLE action_tasks ADD COLUMN envelope TEXT')
            if 'outcome' not in {x[1] for x in d.execute('PRAGMA table_info(action_operations)')}:d.execute("ALTER TABLE action_operations ADD COLUMN outcome TEXT NOT NULL DEFAULT 'intent'")
    async def _snapshot(self,a,wid):
        w=await self.runtime.workspace(a,wid)
        snap=await asyncio.to_thread(self.runtime.files(w).snapshot)
        return w,snap
    async def get(self,a,wid,tid):
        await self.runtime.workspace(a,wid);identifier(tid)
        row=await self.runtime.store.run(lambda d:d.execute('SELECT * FROM action_tasks WHERE id=? AND actor=? AND workspace=?',(tid,a,wid)).fetchone())
        if row is None:raise CodingError('task_not_found',404)
        return dict(row)
    async def admit(self,a,wid,turn_key,spec=None):
        actor(a);identifier(wid)
        if type(turn_key) is not str or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}',turn_key):raise CodingError('idempotency_required')
        spec={} if spec is None else spec
        if type(spec) is not dict or set(spec)-{'profile','paths','tests','db_path','schema_version','tables','task_id','new_task'}:raise CodingError('invalid_verification')
        if 'new_task' in spec and type(spec['new_task']) is not bool:raise CodingError('invalid_verification')
        if spec.get('task_id'):
            if set(spec)-{'task_id'}:raise CodingError('task_contract_immutable',409)
            task=await self.get(a,wid,spec['task_id'])
            if task['expires']<=self.clock() or task['state'] in ('superseded','expired'):raise CodingError('task_expired',409)
        else:task=None
        profile=spec.get('profile','observed_steps')
        if profile not in PROFILES:raise CodingError('verification_unavailable',503)
        permitted={'observed_steps':set(),'read':{'paths'},'artifact':{'paths'},'tests':{'tests'},'migration':{'db_path','schema_version','tables'},'conversation':set(),'plan_only':set()}[profile]|{'profile','new_task'}
        if not task and set(spec)-permitted:raise CodingError('invalid_verification')
        if profile in ('conversation','plan_only'):return None
        async with self.runtime.lock(wid):
            w,snap=await self._snapshot(a,wid);blobs=bytes_map(snap);config={}
            if profile in ('read','artifact'):
                paths=safe_paths(spec.get('paths',[]));config['paths']=[path_id(p) for p in paths]
            if profile=='tests':
                paths=safe_paths(spec.get('tests',[]))
                if any(not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_/]*\.py',p) or p not in blobs for p in paths):raise CodingError('verification_tests_unavailable',409)
                config['tests']={path_id(p):digest(blobs[p]) for p in paths}
            if profile=='migration':
                paths=safe_paths([spec.get('db_path')]);schema=spec.get('schema_version');tables=spec.get('tables')
                if type(schema) is not int or not 0<=schema<=100000 or type(tables) is not dict or not 1<=len(tables)<=8:raise CodingError('invalid_verification')
                for name,rule in tables.items():
                    if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]{0,63}',name) or type(rule) is not dict or set(rule)-{'columns','minimum_rows','row_digest'}:raise CodingError('invalid_verification')
                    columns=rule.get('columns');minimum=rule.get('minimum_rows')
                    if type(columns) is not dict or not 1<=len(columns)<=32 or any(not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]{0,63}',c) or typ not in ('TEXT','INTEGER','REAL','BLOB','NUMERIC') for c,typ in columns.items()) or type(minimum) is not int or not 0<=minimum<=10000000:raise CodingError('invalid_verification')
                    if 'row_digest' in rule and (type(rule['row_digest']) is not str or not re.fullmatch('[a-f0-9]{64}',rule['row_digest'])):raise CodingError('invalid_verification')
                config={'db':path_id(paths[0]),'schema':schema,'tables':tables}

            token_digest=fingerprint(spec);now=self.clock()
            def reserve(d):
                d.execute('BEGIN IMMEDIATE')
                previous=d.execute('SELECT * FROM action_turns WHERE actor=? AND turn_key=?',(a,turn_key)).fetchone()
                if previous:
                    if previous['digest']!=token_digest or previous['workspace']!=wid:raise CodingError('idempotency_conflict',409)
                    return previous['task']
                active=d.execute("SELECT * FROM action_tasks WHERE actor=? AND workspace=? AND state='active'",(a,wid)).fetchone()
                if task:
                    tid=task['id']
                elif active and not spec:
                    if active['expires']<=now:raise CodingError('task_expired',409)
                    tid=active['id']
                else:
                    if active and not spec.get('new_task'):raise CodingError('active_task_requires_explicit_revision',409)
                    if active:d.execute("UPDATE action_tasks SET state='superseded' WHERE id=?",(active['id'],))
                    if d.execute('SELECT count(*) FROM action_tasks').fetchone()[0]>=5000:raise CodingError('task_capacity',429)
                    tid=str(uuid4());d.execute('INSERT INTO action_tasks(id,actor,workspace,profile,config,state,created,expires) VALUES(?,?,?,?,?,?,?,?)',(tid,a,wid,profile,json.dumps(config),'active',now,now+3600))
                if d.execute('SELECT count(*) FROM action_turns WHERE task=?',(tid,)).fetchone()[0]>=128 or d.execute('SELECT count(*) FROM action_turns').fetchone()[0]>=10000:raise CodingError('task_turn_limit',429)
                d.execute('INSERT INTO action_turns VALUES(?,?,?,?,?)',(a,wid,turn_key,tid,token_digest));return tid
            tid=await self.runtime.store.run(reserve)
        return await self.get(a,wid,tid)
    async def intent(self,a,wid,tid,key,skill,args):
        t=await self.get(a,wid,tid)
        if t['state']!='active' or t['expires']<=self.clock():raise CodingError('task_not_active',409)
        ih=fingerprint({'workspace':wid,'skill':skill,'input':args})
        def reserve(d):
            d.execute('BEGIN IMMEDIATE')
            row=d.execute('SELECT * FROM action_operations WHERE task=? AND step_key=?',(tid,key)).fetchone()
            if row:
                if row['input_hash']!=ih or row['skill']!=skill:raise CodingError('idempotency_conflict',409)
                return dict(row)
            if d.execute('SELECT count(*) FROM action_operations WHERE task=?',(tid,)).fetchone()[0]>=64 or d.execute('SELECT count(*) FROM action_operations').fetchone()[0]>=10000:raise CodingError('task_operation_limit',429)
            d.execute('INSERT INTO action_operations(task,step_key,input_hash,skill,job,created) VALUES(?,?,?,?,?,?)',(tid,key,ih,skill,None,self.clock()));return None
        previous=await self.runtime.store.run(reserve)
        # Lost ACK: recover the original durable job, never resubmit on uncertainty.
        row=await self.runtime.store.run(lambda d:d.execute('SELECT * FROM jobs WHERE actor=? AND workspace=? AND idem=?',(a,wid,key)).fetchone())
        if row:
            if row['input_hash']!=ih:raise CodingError('idempotency_conflict',409)
            await self.runtime.store.run(lambda d:d.execute('UPDATE action_operations SET job=? WHERE task=? AND step_key=?',(row['id'],tid,key)))
            return self.runtime.view_job(dict(row))
        if previous and previous['outcome']!='rejected':raise CodingError('operation_outcome_unknown',409)
        return None
    async def link(self,a,wid,tid,key):
        await self.get(a,wid,tid)
        def bind(d):
            d.execute('BEGIN IMMEDIATE')
            row=d.execute('SELECT * FROM jobs WHERE actor=? AND workspace=? AND idem=?',(a,wid,key)).fetchone()
            op=d.execute('SELECT * FROM action_operations WHERE task=? AND step_key=?',(tid,key)).fetchone()
            if row is None or op is None or row['input_hash']!=op['input_hash'] or row['skill']!=op['skill']:raise CodingError('unverified_operation',409)
            d.execute('UPDATE action_operations SET job=? WHERE task=? AND step_key=?',(row['id'],tid,key))
        await self.runtime.store.run(bind)
    async def reject_intent(self,a,wid,tid,key):
        await self.get(a,wid,tid)
        def reject(d):
            d.execute('BEGIN IMMEDIATE')
            if d.execute('SELECT 1 FROM jobs WHERE actor=? AND workspace=? AND idem=?',(a,wid,key)).fetchone() is None:d.execute("UPDATE action_operations SET outcome='rejected' WHERE task=? AND step_key=?",(tid,key))
        await self.runtime.store.run(reject)
    async def _operations(self,a,wid,tid):
        # Reconcile admission gaps from the durable original idempotency key.
        def rows(d):
            d.execute('BEGIN IMMEDIATE')
            ops=d.execute('SELECT * FROM action_operations WHERE task=? ORDER BY created,rowid',(tid,)).fetchall();out=[]
            for op in ops:
                j=d.execute('SELECT * FROM jobs WHERE actor=? AND workspace=? AND idem=?',(a,wid,op['step_key'])).fetchone()
                if j and j['input_hash']==op['input_hash'] and j['skill']==op['skill']:out.append(dict(j))
                else:
                    rejected=op['outcome']=='rejected'
                    # Only a closed server turn + absent durable job proves that
                    # admission never happened. A missing ACK alone proves nothing.
                    turns=d.execute('SELECT t.turn_key,r.state FROM action_turns t JOIN turns r ON r.actor=t.actor AND r.idem=t.turn_key WHERE t.task=? AND t.actor=? AND t.workspace=?',(tid,a,wid)).fetchall()
                    for turn in turns:
                        prefix='agent-'+hashlib.sha256((a+'\0'+wid+'\0'+turn['turn_key']).encode()).hexdigest()[:32]+'-'
                        if op['step_key'].startswith(prefix) and turn['state']!='running':rejected=True
                    if rejected:d.execute("UPDATE action_operations SET outcome='rejected' WHERE task=? AND step_key=?",(tid,op['step_key']))
                    out.append({'state':'failed' if rejected else 'unknown','skill':op['skill'],'created':op['created']})
            return out
        return await self.runtime.store.run(rows)
    async def _isolated(self,snapshot,code):
        async def discard(*args):pass
        nonce=secrets.token_hex(16);code=code.replace('__HARMONIKA_VERIFIER_NONCE__',nonce)
        # A separate bounded verifier lane avoids both unbounded Docker fan-out
        # and lock-order deadlock with original jobs holding execution slots.
        async with self._verifier_slots:
            out=await self.runtime.sandbox.run(snapshot,'_internal.verify_python',{'code':code,'timeout_seconds':30},str(uuid4()),discard,deadline_seconds=65)
        if type(out) is not dict or out.get('status')!='completed' or out.get('exit_code')!=0 or out.get('stdout_truncated') or out.get('changed_state_unknown'):return None
        try:
            v=json.loads(out.get('stdout',''));return v if type(v) is dict and v.pop('_verification_nonce',None)==nonce else None
        except (ValueError,TypeError):return None
    async def assess(self,a,wid,tid,execute_checks=True):
        t=await self.get(a,wid,tid)
        async with self.runtime.lock(wid):
            w,snap=await self._snapshot(a,wid);rev=revision(snap);blobs=bytes_map(snap);names={path_id(n):n for n in blobs};config=json.loads(t['config']);ops=await self._operations(a,wid,tid)
            checks={};metrics={};missing=[];now=self.clock();verified_at=t.get('verified_at');pending=any(j['state'] in ('unknown','queued','running','approval_required','interrupted','timeout') for j in ops)
            valid_task=t['state'] not in ('superseded','expired') and t['expires']>now
            if not valid_task:missing.append('task_expired')
            completed=[j for j in ops if j['state']=='completed']
            profile=t['profile']
            if profile=='observed_steps':missing.append('acceptance_profile_required')
            if profile=='read':
                reads=[j for j in completed if j['skill'] in ('fs.read','code.read')]
                actual={path_id(json.loads(j['payload']).get('path','')) for j in reads if j['version']==w['version']}
                checks['read']=bool(reads) and all(pid in actual and pid in names for pid in config['paths'])
                if checks['read']:metrics['read']={'files':len(config['paths']),'bytes':sum(len(blobs[names[p]]) for p in config['paths'])}
                if not checks['read']:missing.append('read')
            if profile=='artifact':
                writes=[j for j in completed if j['skill'] in ('fs.write','code.write','code.edit')]
                current={}
                for j in writes:
                    args=json.loads(j['payload']);result=json.loads(j['result'] or '{}');pid=path_id(args.get('path',''))
                    current[pid]=result.get('sha256')
                checks['artifact']=all(pid in current and pid in names and current[pid]==digest(blobs[names[pid]]) for pid in config['paths'])
                if checks['artifact']:metrics['artifact']={'files':len(config['paths']),'bytes':sum(len(blobs[names[p]]) for p in config['paths'])}
                if not checks['artifact']:missing.append('artifact')
            if profile in ('tests','migration'):
                # An independently rerun check is execution too: require exact-revision
                # approval from a completed original execution, never self-approve.
                jobs=[j for j in completed if j['skill']==('test.run' if profile=='tests' else 'exec.python') and json.loads(j.get('result') or '{}').get('result_workspace_version',j['version'])==w['version'] and j['created']>=t['created']]
                failed_later=any(j['state']!='completed' and j.get('created',0)>=max((x['created'] for x in jobs),default=0) for j in ops)
                approved=bool(jobs) and not failed_later and json.loads(w['permissions']).get('execute',False)
                if profile=='tests':
                    unchanged=all(pid in names and digest(blobs[names[pid]])==h for pid,h in config['tests'].items())
                    if not unchanged:missing.append('protected_assertions_changed')
                    eligible=approved and unchanged and supported_python(snap) and all(json.loads(j['payload']).get('runner')=='python_unittest' for j in jobs[-1:])
                    if not supported_python(snap):missing.append('unsupported_verification_source')
                    code=test_program([names[pid] for pid in config['tests']]) if unchanged else ''
                else:
                    eligible=approved and config['db'] in names
                    code=migration_program(names[config['db']],config['schema'],config['tables']) if config['db'] in names else ''
                old=json.loads(t['checks'])
                encoded_old=t.get('envelope') or ''
                authentic=bool(encoded_old and t.get('mac') and hmac.compare_digest(hmac.new(self._key,encoded_old.encode(),hashlib.sha256).hexdigest(),t['mac']))
                try:old_proof=json.loads(encoded_old) if authentic else {}
                except ValueError:old_proof={}
                cached=authentic and all(old_proof.get(k)==v for k,v in {'task_id':tid,'workspace_id':wid,'revision':rev,'workspace_version':w['version'],'profile':profile,'owner_hash':digest(a.encode()),'config_hash':fingerprint(config)}.items()) and old_proof.get('checks')==old and old_proof.get('verified_at')==verified_at and t['revision']==rev and t['version']==w['version'] and t.get('verified_at') is not None and 0<=now-t['verified_at']<=MAX_AGE
                if eligible and execute_checks:
                    facts=await self._isolated(snap,code);verified_at=self.clock()
                    if profile=='tests':checks['tests']=bool(facts) and type(facts.get('collected')) is int and facts['collected']>0 and facts.get('passed')==facts['collected'] and facts.get('failed')==0 and facts.get('skipped')==0 and facts.get('awaited') is True
                    else:checks['migration']=bool(facts) and facts.get('schema')==config['schema'] and facts.get('integrity')=='ok' and facts.get('tables_ok') is True
                    if checks.get(profile):metrics[profile]={k:v for k,v in facts.items() if k in ('collected','passed','failed','skipped','awaited','schema','integrity','tables_ok')}
                else:
                    checks[profile]=bool(eligible and cached and old.get(profile))
                    if checks[profile]:metrics[profile]=old_proof.get('metrics',{}).get(profile,{})
                if not checks.get(profile):missing.append(profile)
            if profile in ('read','artifact'):verified_at=self.clock()
            if self.clock()>=t['expires'] and 'task_expired' not in missing:missing.append('task_expired')
            if pending:missing.append('reconciliation_or_approval')
            # Zero operations, stale/later failures or cancellation never prove work.
            if not completed:missing.append('actual_operations_required')
            if ops and ops[-1]['state'] not in ('completed',):missing.append('latest_operation_not_successful')
            passed=valid_task and not missing and profile!='observed_steps'
            view={'task_id':tid,'profile':profile,'workspace_id':wid,'revision':rev,'workspace_version':w['version'],'status':'completed' if passed else 'waiting' if pending else 'unverified','checks':checks,'metrics':metrics,'missing':sorted(set(missing)),'provenance':'independently_verified' if passed else 'server_observed','operation_count':len(ops),'verified_at':verified_at,'observed_at':self.clock(),'owner_hash':digest(a.encode()),'config_hash':fingerprint(config)}
            encoded=json.dumps(view,sort_keys=True,separators=(',',':'));mac=hmac.new(self._key,encoded.encode(),hashlib.sha256).hexdigest()
            def settle(d):
                cur=d.execute('SELECT version FROM workspaces WHERE id=? AND actor=?',(wid,a)).fetchone()
                if cur is None or cur['version']!=w['version']:raise CodingError('workspace_changed',409)
                d.execute("UPDATE action_tasks SET revision=?,version=?,checks=?,verified_at=?,mac=?,envelope=? WHERE id=? AND actor=?",(rev,w['version'],json.dumps(checks),verified_at,mac,encoded,tid,a))
            await self.runtime.store.run(settle)
            return {**view,'signature':mac}
    async def prune(self):
        now=self.clock()
        def clean(d):
            d.execute('BEGIN IMMEDIATE')
            d.execute("UPDATE action_tasks SET state='expired' WHERE state='active' AND expires<=?",(now,))
            rows=d.execute("SELECT id FROM action_tasks WHERE created<? AND NOT EXISTS (SELECT 1 FROM action_operations o JOIN jobs j ON j.id=o.job WHERE o.task=action_tasks.id AND j.state IN ('queued','running','approval_required')) LIMIT 128",(now-86400,)).fetchall()
            for row in rows:
                d.execute('DELETE FROM action_turns WHERE task=?',(row['id'],));d.execute('DELETE FROM action_operations WHERE task=?',(row['id'],));d.execute('DELETE FROM action_tasks WHERE id=?',(row['id'],))
            return len(rows)
        return await self.runtime.store.run(clean)
    async def reply(self,a,wid,proof):
        text=self.render(proof)
        if proof['profile']!='read' or proof['status']!='completed':return text
        async with self.runtime.lock(wid):
            task=await self.get(a,wid,proof['task_id']);w,snapshot=await self._snapshot(a,wid)
            if w['version']!=proof['workspace_version'] or revision(snapshot)!=proof['revision']:raise CodingError('workspace_changed',409)
            blobs=bytes_map(snapshot);wanted=set(json.loads(task['config'])['paths']);parts=[]
            for name,blob in blobs.items():
                if path_id(name) not in wanted:continue
                try:content=blob.decode('utf-8')
                except UnicodeError:content='[Berkas biner; isi teks tidak diklaim.]'
                parts.append('Data berkas '+name+':\n'+self.runtime.redact(content[:16000])+('\n[Cuplikan dibatasi; isi lengkap tidak ditampilkan.]' if len(content)>16000 else ''))
            return text+'\n\n'+'\n\n'.join(parts)
    @staticmethod
    def render(proof):
        labels={'read':'pembacaan berkas','artifact':'readback dan checksum berkas','tests':'tes asli pada revisi terkini','migration':'schema, integritas dan jumlah data SQLite'}
        done=[labels.get(k,k) for k,v in proof['checks'].items() if v]
        if proof['status']=='completed':return 'Selesai sesuai kontrak. Terverifikasi: '+'; '.join(done)+'.'
        gaps={'acceptance_profile_required':'kriteria penyelesaian tugas belum ditetapkan','reconciliation_or_approval':'approval atau rekonsiliasi operasi','actual_operations_required':'eksekusi nyata','latest_operation_not_successful':'operasi terakhir belum berhasil','unsupported_verification_source':'kode memakai fitur di luar verifier Python terbatas','protected_assertions_changed':'tes asli berubah','task_expired':'kontrak kedaluwarsa',**labels}
        return 'Belum selesai. '+('Terverifikasi: '+'; '.join(done)+'. ' if done else '')+'Masih diperlukan: '+'; '.join(gaps.get(k,k) for k in proof['missing'])+'.'
