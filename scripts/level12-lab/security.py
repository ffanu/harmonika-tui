"""Exam fixture authentication; independent protected code, no candidate secrets."""
import hashlib,hmac,json,re,secrets,sqlite3,time
from contextlib import contextmanager
from pathlib import Path
class AuthError(Exception):
 def __init__(self,status,code):self.status=status;self.code=code;super().__init__(code)
class Security:
 def __init__(self,db_path,config_path,origin):
  self.db_path=str(db_path);self.origin=origin;self.config=json.loads(Path(config_path).read_text());self.failures={}
  with self.db() as c:c.execute('CREATE TABLE IF NOT EXISTS sessions(token TEXT PRIMARY KEY, username TEXT NOT NULL, csrf TEXT NOT NULL, expires REAL NOT NULL)')
 @contextmanager
 def db(self):
  c=sqlite3.connect(self.db_path,timeout=.2);c.execute('PRAGMA busy_timeout=200')
  try:
   with c:yield c
  finally:c.close()
 def check_origin(self,headers):
  if headers.get('Origin')!=self.origin:raise AuthError(403,'origin_invalid')
 def login(self,data,headers,peer='local'):
  self.check_origin(headers)
  if not isinstance(data,dict) or set(data)!={'username','password'} or not all(isinstance(data[k],str) for k in data):raise AuthError(422,'login_invalid')
  u=data['username'];k=(u[:80],peer);now=time.time();f=self.failures.get(k,(0,0))
  if f[1]>now:raise AuthError(429,'login_throttled')
  rec=self.config['users'].get(u);salt=(rec or self.config['users']['admin'])['salt'];computed=hashlib.pbkdf2_hmac('sha256',data['password'].encode(),bytes.fromhex(salt),180000).hex()
  if not rec or not hmac.compare_digest(computed,rec['hash']):
   if len(self.failures)>256:self.failures.clear()
   self.failures[k]=(f[0]+1,now+min(60,2**min(f[0],6)));raise AuthError(401,'login_failed')
  self.failures.pop(k,None);token=secrets.token_urlsafe(32);csrf=secrets.token_urlsafe(32)
  with self.db() as c:
   c.execute('DELETE FROM sessions WHERE expires<?',(now,));c.execute('INSERT INTO sessions VALUES(?,?,?,?)',(hashlib.sha256(token.encode()).hexdigest(),u,csrf,now+3600))
  return {'user':{'username':u,'role':rec['role']},'csrf':csrf},'hl12_session='+token+'; Path=/; Secure; HttpOnly; SameSite=Strict; Max-Age=3600'
 def resolve(self,headers):
  if headers.get('X-Role') is not None or headers.get('X-Owner') is not None:raise AuthError(403,'forged_identity')
  cookie=headers.get('Cookie','');m=re.search(r'(?:^|;\s*)hl12_session=([A-Za-z0-9_-]{20,100})(?:;|$)',cookie)
  if not m:raise AuthError(401,'unauthenticated')
  with self.db() as c:row=c.execute('SELECT username,csrf,expires FROM sessions WHERE token=?',(hashlib.sha256(m[1].encode()).hexdigest(),)).fetchone()
  if not row or row[2]<time.time() or row[0] not in self.config['users']:raise AuthError(401,'unauthenticated')
  return {'username':row[0],'role':self.config['users'][row[0]]['role']},row[1]
 def require_mutation(self,headers,csrf):
  self.check_origin(headers)
  if not hmac.compare_digest(headers.get('X-CSRF-Token',''),csrf):raise AuthError(403,'csrf_invalid')
 def logout(self,headers):
  _,csrf=self.resolve(headers);self.require_mutation(headers,csrf);m=re.search(r'(?:^|;\s*)hl12_session=([A-Za-z0-9_-]{20,100})(?:;|$)',headers.get('Cookie',''))
  with self.db() as c:c.execute('DELETE FROM sessions WHERE token=?',(hashlib.sha256(m[1].encode()).hexdigest(),))
  return 'hl12_session=; Path=/; Secure; HttpOnly; SameSite=Strict; Max-Age=0'
