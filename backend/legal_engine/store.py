"""Private tenant records: authenticated AES-256-GCM envelopes, never plain case JSON."""
import contextlib,hashlib,json,os,re,sqlite3
from pathlib import Path
from uuid import uuid4
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from .common import require,DomainError,identifier,now_iso

def canonical(v):return json.dumps(v,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)
RECORD_QUOTA=2000
LIST_SCAN_BOUND=RECORD_QUOTA+1
LIMITS={'records_per_tenant':RECORD_QUOTA,'list_limit_max':RECORD_QUOTA,'list_scan_rows':LIST_SCAN_BOUND}
def limit_mismatches(claims):
 require(type(claims) is dict,'invalid_claims');m=[]
 for k,v in claims.items():
  require(type(k) is str and type(v) is int and v>=0,'invalid_claims')
  if v>RECORD_QUOTA:m.append({'claim':k,'claimed_bound':v,'enforced_bound':RECORD_QUOTA,'supported':False})
 return m
class Store:
 def __init__(self,path,key):
  require(type(key) is bytes and len(key)==32,'encryption_key_required');self.key=key;self.path=Path(path);self.path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
  require(not self.path.is_symlink(),'unsafe_database');self.timeout=10;self.aes=AESGCM(key)
  with self.db() as d:
   d.execute('PRAGMA journal_mode=WAL');d.executescript('''CREATE TABLE IF NOT EXISTS records(tenant TEXT,kind TEXT,id TEXT,version INTEGER,data BLOB,PRIMARY KEY(tenant,kind,id));
CREATE TABLE IF NOT EXISTS requests(id TEXT PRIMARY KEY,actor TEXT,idem TEXT,digest TEXT,state TEXT,result BLOB,created TEXT,UNIQUE(actor,idem));
CREATE TABLE IF NOT EXISTS events(request_id TEXT,sequence INTEGER,type TEXT,timestamp TEXT,data BLOB,PRIMARY KEY(request_id,sequence));
CREATE TABLE IF NOT EXISTS audit(tenant TEXT,actor TEXT,type TEXT,timestamp TEXT,data BLOB);''')
  os.chmod(self.path,0o600)
 @staticmethod
 def tenant(actor):
  require(type(actor) is str and re.fullmatch(r'api-token:[1-9][0-9]{0,18}',actor) is not None,'invalid_principal',403)
  return hashlib.sha256(('legal-v1:'+actor).encode()).hexdigest()
 def seal(self,value,aad):
  nonce=os.urandom(12);raw=canonical(value).encode();require(len(raw)<=1048576,'payload_too_large',413);return nonce+self.aes.encrypt(nonce,raw,aad.encode())
 def open(self,value,aad):
  try:return json.loads(self.aes.decrypt(value[:12],value[12:],aad.encode()))
  except Exception:raise DomainError('encrypted_record_invalid',503) from None
 @contextlib.contextmanager
 def db(self):
  d=sqlite3.connect(self.path,timeout=self.timeout);d.row_factory=sqlite3.Row
  try:
   with d:yield d
  except sqlite3.OperationalError as e:
   m=str(e).lower()
   if 'locked' in m or 'busy' in m:raise DomainError('legal_busy',429,{'reason':'database_busy'}) from None
   raise
  finally:d.close()
 def transaction(self,fn):
  with self.db() as d:d.execute('BEGIN IMMEDIATE');return fn(d)
class Context:
 def __init__(self,store,db,actor,*,sources=None,evidence=None,read_only=False,now=None):self.store=store;self.db=db;self.actor=actor;self.tenant=store.tenant(actor);self.sources=sources;self.evidence=evidence;self.read_only=read_only;self.now=now or now_iso()
 def _kind(self,kind):require(type(kind) is str and re.fullmatch('[a-z][a-z0-9_]{0,63}',kind) is not None,'invalid_kind');return kind
 def _aad(self,kind,id):return 'legal.record:'+self.tenant+':'+kind+':'+id
 def get(self,kind,id):
  self._kind(kind);identifier(id);r=self.db.execute('SELECT data FROM records WHERE tenant=? AND kind=? AND id=?',(self.tenant,kind,id)).fetchone();require(r is not None,'not_found',404);return self.store.open(r[0],self._aad(kind,id))
 def list(self,kind,where=None,limit=None):
  self._kind(kind);require(where is None or type(where) is dict,'invalid_where')
  if limit is not None:require(type(limit) is int and 1<=limit<=RECORD_QUOTA,'invalid_limit')
  rows=self.db.execute('SELECT id,data FROM records WHERE tenant=? AND kind=? ORDER BY id LIMIT ?',(self.tenant,kind,LIST_SCAN_BOUND)).fetchall()
  if len(rows)>RECORD_QUOTA:raise DomainError('store_bound_mismatch',413,{'tenant_kind_rows':len(rows),'enforced_bound':RECORD_QUOTA})
  out=[self.store.open(r['data'],self._aad(kind,r['id'])) for r in rows]
  if where:out=[r for r in out if all(r.get(k)==v for k,v in where.items())]
  if limit is not None:require(len(out)<=limit,'query_limit_exceeded',413)
  return out
 def create(self,kind,data,id=None):
  require(not self.read_only,'mutation_not_authorized',403);self._kind(kind);id=identifier(id) if id is not None else str(uuid4());require(type(data) is dict and not(set(data)&{'id','version','tenant','actor','created_at','updated_at'}),'reserved_fields')
  n=self.db.execute('SELECT count(*) FROM records WHERE tenant=?',(self.tenant,)).fetchone()[0]
  if n>=RECORD_QUOTA:raise DomainError('record_quota',429,{'tenant_records':n,'record_quota':RECORD_QUOTA})
  out={**data,'id':id,'version':1,'created_at':self.now,'updated_at':self.now}
  try:self.db.execute('INSERT INTO records VALUES(?,?,?,?,?)',(self.tenant,kind,id,1,self.store.seal(out,self._aad(kind,id))))
  except sqlite3.IntegrityError:raise DomainError('already_exists',409) from None
  return out
 def update(self,kind,id,changes,expected_version=None):
  require(not self.read_only,'mutation_not_authorized',403);old=self.get(kind,id);require(type(changes) is dict and not(set(changes)&{'id','version','tenant','actor','created_at','updated_at'}),'reserved_fields')
  if expected_version is not None:require(type(expected_version) is int and old['version']==expected_version,'version_conflict',409)
  out={**old,**changes,'version':old['version']+1,'updated_at':self.now};n=self.db.execute('UPDATE records SET version=?,data=? WHERE tenant=? AND kind=? AND id=? AND version=?',(out['version'],self.store.seal(out,self._aad(kind,id)),self.tenant,kind,id,old['version'])).rowcount;require(n==1,'version_conflict',409);return out
 def event(self,kind,data):self.db.execute('INSERT INTO audit VALUES(?,?,?,?,?)',(self.tenant,self.actor,kind,self.now,self.store.seal({'keys':sorted(data)[:30]},'legal.audit:'+self.tenant)))
