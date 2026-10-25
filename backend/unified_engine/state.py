"""Encrypted, principal-bound durable jobs and session history; no access tokens stored."""
import hashlib,hmac,json,re,time,uuid
ADMISSION_BYTES=80*1048576  # Reserve >=20MiB for terminal results and deletion tombstones.
try:from core_engine.state import Store
except ModuleNotFoundError:from backend.core_engine.state import Store

class Error(ValueError):
 def __init__(self,code,status=400):self.code=code;self.status=status;super().__init__(code)
def require(ok,code,status=400):
 if not ok:raise Error(code,status)
def uid(value):return type(value) is str and re.fullmatch(r'[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}',value) is not None
def encoded(v):
 try:raw=json.dumps(v,ensure_ascii=False,allow_nan=False,sort_keys=True,separators=(',',':')).encode()
 except (ValueError,TypeError,RecursionError):raise Error('invalid_json') from None
 require(len(raw)<=196608,'state_payload_limit',413);return raw

class State:
 def __init__(self,path,key):
  self.store=Store(path,key)
  with self.store._transaction() as db:
   db.executescript('''CREATE TABLE IF NOT EXISTS unified_jobs(id TEXT PRIMARY KEY,actor TEXT NOT NULL,idem TEXT NOT NULL,digest TEXT NOT NULL,status TEXT NOT NULL,sealed BLOB NOT NULL,created REAL NOT NULL,expires REAL NOT NULL,UNIQUE(actor,idem));
   CREATE TABLE IF NOT EXISTS unified_sessions(id TEXT PRIMARY KEY,actor TEXT NOT NULL,sealed BLOB NOT NULL,expires REAL NOT NULL);''')
 def owner(self,actor):
  require(type(actor) is str and re.fullmatch(r'api-token:[1-9][0-9]{0,18}',actor),'invalid_principal',403)
  return self.store._hash('unified_actor',actor)
 def _seal(self,v,owner,kind,id):return self.store._seal(v,'unified',owner,kind,id)
 def _open(self,row,kind):return self.store._unseal(row['sealed'],'unified',row['actor'],kind,row['id'])
 def cleanup(self,db):
  for t in ('unified_jobs','unified_sessions'):db.execute('DELETE FROM '+t+' WHERE expires<?',(time.time(),))
 def admission_budget(self,db,extra=0):
  used=(db.execute('PRAGMA page_count').fetchone()[0]-db.execute('PRAGMA freelist_count').fetchone()[0])*db.execute('PRAGMA page_size').fetchone()[0]
  require(used+extra<=ADMISSION_BYTES,'state_capacity',429)
 def create(self,actor,payload,key):
  owner=self.owner(actor);require(type(key) is str and re.fullmatch(r'[A-Za-z0-9_-]{8,128}',key),'idempotency_required')
  digest=self.store._hash('unified_request',encoded(payload).decode());token=self.store._hash('unified_idem',owner,key)
  with self.store._transaction() as db:
   self.cleanup(db);old=db.execute('SELECT * FROM unified_jobs WHERE actor=? AND idem=?',(owner,token)).fetchone()
   if old:
    require(hmac.compare_digest(old['digest'],digest),'idempotency_conflict',409)
    return {**self._open(old,'job'),'replayed':True}
   require(not db.execute("SELECT 1 FROM unified_jobs WHERE actor=? AND status IN ('queued','running')",(owner,)).fetchone(),'actor_busy',409)
   require(db.execute('SELECT COUNT(*) FROM unified_jobs WHERE actor=?',(owner,)).fetchone()[0]<50,'job_quota',429)
   require(db.execute('SELECT COUNT(*) FROM unified_jobs').fetchone()[0]<1000,'global_job_capacity',429)
   id=str(uuid.uuid4());now=time.time();v={'id':id,'status':'queued','session_id':payload.get('session_id'),'created_at':now,'updated_at':now,'sequence':1,'events':[{'sequence':1,'type':'queued','timestamp':now}],'request':payload,'result':None}
   sealed=self._seal(v,owner,'job',id);self.admission_budget(db,len(sealed));db.execute('INSERT INTO unified_jobs VALUES(?,?,?,?,?,?,?,?)',(id,owner,token,digest,'queued',sealed,now,now+86400))
   return v
 def replay(self,actor,payload,key):
  owner=self.owner(actor);require(type(key) is str and re.fullmatch(r'[A-Za-z0-9_-]{8,128}',key),'idempotency_required')
  digest=self.store._hash('unified_request',encoded(payload).decode());token=self.store._hash('unified_idem',owner,key)
  with self.store._transaction() as db:
   old=db.execute('SELECT * FROM unified_jobs WHERE actor=? AND idem=? AND expires>?',(owner,token,time.time())).fetchone()
   if old:
    require(hmac.compare_digest(old['digest'],digest),'idempotency_conflict',409);return {**self._open(old,'job'),'replayed':True}
  return None
 def get(self,actor,id):
  owner=self.owner(actor);require(uid(id),'invalid_job_id')
  with self.store._transaction() as db:
   row=db.execute('SELECT * FROM unified_jobs WHERE id=? AND actor=? AND expires>?',(id,owner,time.time())).fetchone()
   require(row is not None,'not_found',404);return self._open(row,'job')
 def transition(self,actor,id,status,result=None):
  require(status in ('running','completed','failed','stopped','recovery_required'),'invalid_status')
  owner=self.owner(actor)
  with self.store._transaction() as db:
   row=db.execute('SELECT * FROM unified_jobs WHERE id=? AND actor=?',(id,owner)).fetchone();require(row is not None,'not_found',404)
   v=self._open(row,'job')
   if v['status'] not in ('queued','running'):return v
   v.update(status=status,updated_at=time.time(),sequence=v['sequence']+1,result=result)
   v['events'].append({'sequence':v['sequence'],'type':status,'timestamp':v['updated_at']})
   encoded(v);db.execute('UPDATE unified_jobs SET status=?,sealed=? WHERE id=? AND actor=?',(status,self._seal(v,owner,'job',id),id,owner));return v
 def recover(self):
  with self.store._transaction() as db:
   self.cleanup(db);rows=db.execute("SELECT * FROM unified_jobs WHERE status IN ('queued','running')").fetchall()
   for row in rows:
    v=self._open(row,'job');v.update(status='recovery_required',updated_at=time.time(),sequence=v['sequence']+1,result={'error':{'code':'service_restarted_no_automatic_replay'}})
    v['events'].append({'sequence':v['sequence'],'type':'recovery_required','timestamp':v['updated_at']})
    db.execute('UPDATE unified_jobs SET status=?,sealed=? WHERE id=?',('recovery_required',self._seal(v,row['actor'],'job',row['id']),row['id']))
   return len(rows)
 def session(self,actor,id=None):
  owner=self.owner(actor)
  with self.store._transaction() as db:
   self.cleanup(db)
   if id is not None:
    require(uid(id),'invalid_session_id');row=db.execute('SELECT * FROM unified_sessions WHERE actor=? AND id=?',(owner,id)).fetchone();require(row is not None,'not_found',404);return self._open(row,'session')
   require(db.execute('SELECT COUNT(*) FROM unified_sessions WHERE actor=?',(owner,)).fetchone()[0]<10,'session_quota',429)
   require(db.execute('SELECT COUNT(*) FROM unified_sessions').fetchone()[0]<1000,'session_capacity',429)
   self.admission_budget(db,4096)
   id=str(uuid.uuid4());v={'id':id,'messages':[],'created_at':time.time()};db.execute('INSERT INTO unified_sessions VALUES(?,?,?,?)',(id,owner,self._seal(v,owner,'session',id),time.time()+604800));return v
 def append(self,actor,id,user,assistant):
  owner=self.owner(actor)
  with self.store._transaction() as db:
   row=db.execute('SELECT * FROM unified_sessions WHERE actor=? AND id=? AND expires>?',(owner,id,time.time())).fetchone();require(row is not None,'not_found',404);v=self._open(row,'session')
   v['messages']=(v['messages']+[{'role':'user','content':user},{'role':'assistant','content':assistant}])[-20:]
   while len(encoded(v))>48000:v['messages']=v['messages'][2:]
   db.execute('UPDATE unified_sessions SET sealed=?,expires=? WHERE id=? AND actor=?',(self._seal(v,owner,'session',id),time.time()+604800,id,owner))
 def public(self,v):return {k:value for k,value in v.items() if k!='request'}
