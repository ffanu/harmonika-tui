"""Tenant-bound transactional records. No connector or model can choose a tenant."""
import contextlib,hashlib,json,os,re,sqlite3
from pathlib import Path
from uuid import uuid4
from .common import DomainError,require,identifier,now_iso
MAX_RECORDS=10000
class Store:
    def __init__(self,path):
        self.path=Path(path);self.path.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
        require(not self.path.is_symlink(),'unsafe_database')
        with self.db() as d:
            d.execute('PRAGMA journal_mode=WAL')
            d.executescript('''CREATE TABLE IF NOT EXISTS tenants(id TEXT PRIMARY KEY,revision INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS records(tenant TEXT NOT NULL,kind TEXT NOT NULL,id TEXT NOT NULL,version INTEGER NOT NULL,data TEXT NOT NULL,PRIMARY KEY(tenant,kind,id));
CREATE TABLE IF NOT EXISTS requests(id TEXT PRIMARY KEY,actor TEXT NOT NULL,tenant TEXT NOT NULL,skill TEXT NOT NULL,payload TEXT NOT NULL,digest TEXT NOT NULL,idem TEXT NOT NULL,state TEXT NOT NULL,input_hash TEXT NOT NULL,revision INTEGER NOT NULL,created TEXT NOT NULL,expires TEXT NOT NULL,result TEXT,error TEXT,UNIQUE(actor,idem));
CREATE TABLE IF NOT EXISTS events(request_id TEXT NOT NULL,sequence INTEGER NOT NULL,type TEXT NOT NULL,timestamp TEXT NOT NULL,data TEXT NOT NULL,PRIMARY KEY(request_id,sequence));
CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY AUTOINCREMENT,tenant TEXT NOT NULL,actor TEXT NOT NULL,request_id TEXT,skill TEXT,type TEXT NOT NULL,timestamp TEXT NOT NULL,data TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS idx_requests_actor_created ON requests(actor,created);''')
        os.chmod(self.path,0o600)
    @contextlib.contextmanager
    def db(self):
        d=sqlite3.connect(self.path,timeout=15);d.row_factory=sqlite3.Row
        try:
            with d:yield d
        finally:d.close()
    def transaction(self,fn):
        with self.db() as d:d.execute('BEGIN IMMEDIATE');return fn(d)
    @staticmethod
    def tenant(actor):
        require(type(actor) is str and re.fullmatch(r'api-token:[1-9][0-9]{0,18}',actor) is not None,'invalid_principal',403)
        return hashlib.sha256(('telco-v1:'+actor).encode()).hexdigest()
class Context:
    def __init__(self,db,tenant,actor,request_id=None,skill=None,now=None,read_only=False):self.read_only=read_only;self.db=db;self.tenant=tenant;self.actor=actor;self.request_id=request_id;self.skill=skill;self.now=now or now_iso()
    def _kind(self,value):require(type(value) is str and re.fullmatch('[a-z][a-z0-9_]{0,63}',value) is not None,'invalid_kind');return value
    def get(self,kind,id):
        self._kind(kind);identifier(id);r=self.db.execute('SELECT data FROM records WHERE tenant=? AND kind=? AND id=?',(self.tenant,kind,id)).fetchone()
        require(r is not None,'not_found',404);return json.loads(r[0])
    def list(self,kind,where=None,limit=1000):
        self._kind(kind);require(type(limit) is int and 1<=limit<=1000,'invalid_limit')
        rows=self.db.execute('SELECT data FROM records WHERE tenant=? AND kind=? ORDER BY id LIMIT 10001',(self.tenant,kind)).fetchall()
        result=[json.loads(r[0]) for r in rows];result=[r for r in result if not where or all(r.get(k)==v for k,v in where.items())]
        require(len(result)<=limit,'query_limit_exceeded',413);return result
    def create(self,kind,data,id=None):
        require(not self.read_only,'mutation_not_authorized',403)
        self._kind(kind);id=identifier(id) if id is not None else str(uuid4());require(type(data) is dict,'invalid_record')
        require(not(set(data)&{'id','version','created_at','updated_at','tenant','actor'}),'reserved_fields')
        require(self.db.execute('SELECT count(*) FROM records WHERE tenant=?',(self.tenant,)).fetchone()[0]<MAX_RECORDS,'tenant_record_quota',429)
        out={**data,'id':id,'version':1,'created_at':self.now,'updated_at':self.now};raw=json.dumps(out,allow_nan=False)
        require(len(raw.encode())<=65536,'record_too_large',413)
        try:self.db.execute('INSERT INTO records VALUES(?,?,?,?,?)',(self.tenant,kind,id,1,raw))
        except sqlite3.IntegrityError:raise DomainError('already_exists',409) from None
        return out
    def update(self,kind,id,changes,expected_version=None):
        require(not self.read_only,'mutation_not_authorized',403)
        old=self.get(kind,id);require(type(changes) is dict and not(set(changes)&{'id','version','created_at','updated_at','tenant','actor'}),'reserved_fields')
        if expected_version is not None:require(type(expected_version) is int and old['version']==expected_version,'version_conflict',409)
        out={**old,**changes,'version':old['version']+1,'updated_at':self.now};raw=json.dumps(out,allow_nan=False);require(len(raw.encode())<=65536,'record_too_large',413)
        n=self.db.execute('UPDATE records SET version=?,data=? WHERE tenant=? AND kind=? AND id=? AND version=?',(out['version'],raw,self.tenant,kind,id,old['version'])).rowcount
        require(n==1,'version_conflict',409);return out
    def event(self,type,data):
        # Audits intentionally contain event keys/counts only, never source payloads.
        safe={'keys':sorted(str(k)[:50] for k in data)[:30]} if isinstance(data,dict) else {'recorded':True}
        self.db.execute('INSERT INTO audit(tenant,actor,request_id,skill,type,timestamp,data) VALUES(?,?,?,?,?,?,?)',(self.tenant,self.actor,self.request_id,self.skill,str(type)[:80],self.now,json.dumps(safe)))
