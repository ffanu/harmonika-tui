"""Private control state: hashed scopes, one-use approvals and mechanic-only audit."""
import hashlib,hmac,json,os,secrets,sqlite3,time
from pathlib import Path

class ControlError(Exception):
 def __init__(self,code,status=400,retry=0):self.code,self.status,self.retry=code,status,retry

def require(ok,code,status=400):
 if not ok:raise ControlError(code,status)

class Control:
 def __init__(self,root,pepper,minute_limit=30,session_limit=100,cooldown=300,clock=time.time):
  self.root=Path(root);self.root.mkdir(parents=True,exist_ok=True,mode=0o700);os.chmod(self.root,0o700)
  self.path=self.root/'control.sqlite';require(not self.path.is_symlink(),'unsafe_state')
  self.pepper=pepper;self.minute_limit=minute_limit;self.session_limit=session_limit;self.cooldown=cooldown;self.clock=clock
  with self.db() as d:
   d.executescript('CREATE TABLE IF NOT EXISTS sessions(id TEXT PRIMARY KEY,actor TEXT,created REAL,count INTEGER DEFAULT 0);CREATE TABLE IF NOT EXISTS limits(id TEXT PRIMARY KEY,start REAL,count INTEGER,blocked_until REAL);CREATE TABLE IF NOT EXISTS approvals(id TEXT PRIMARY KEY,actor TEXT,digest TEXT,expires REAL,used INTEGER DEFAULT 0);CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY, timestamp REAL,skill_id TEXT,status TEXT,error_type TEXT,latency_ms INTEGER,previous_hash TEXT,record_hash TEXT);')
  os.chmod(self.path,0o600)
 def db(self):
  d=sqlite3.connect(self.path,timeout=3);d.execute('PRAGMA journal_mode=WAL');d.execute('PRAGMA busy_timeout=3000');return d
 def scope(self,*parts):return hmac.new(self.pepper,'\x00'.join(parts).encode(),hashlib.sha256).hexdigest()
 def session(self,actor):
  value=secrets.token_hex(24);now=self.clock()
  with self.db() as d:
   d.execute('DELETE FROM sessions WHERE created<?',(now-86400,));d.execute('DELETE FROM approvals WHERE expires<?',(now-300,))
   count=d.execute('SELECT COUNT(*) FROM sessions WHERE actor=? AND created>?',(self.scope(actor),now-300)).fetchone()[0]
   require(count<5,'session_creation_rate_limit',429)
   d.execute('INSERT INTO sessions(id,actor,created) VALUES(?,?,?)',(self.scope(value),self.scope(actor),now))
  return value
 def consume(self,actor,session,target,ip):
  now=self.clock()
  with self.db() as d:
   d.execute('BEGIN IMMEDIATE');r=d.execute('SELECT actor,created,count FROM sessions WHERE id=?',(self.scope(session),)).fetchone()
   require(r and r[0]==self.scope(actor) and now-r[1]<86400,'invalid_session',403)
   require(r[2]<self.session_limit,'session_quota_exceeded',429)
   scopes=[self.scope('actor',actor),self.scope('target',target),self.scope('ip',ip)]
   for scope in scopes:
    row=d.execute('SELECT start,count,blocked_until FROM limits WHERE id=?',(scope,)).fetchone()
    if row and row[2]>now:raise ControlError('ssh_rate_limited',429,max(1,int(row[2]-now)))
    count=row[1] if row and now-row[0]<60 else 0
    if count>=self.minute_limit:
     d.execute('UPDATE limits SET blocked_until=? WHERE id=?',(now+self.cooldown,scope));d.commit()
     raise ControlError('ssh_rate_limited',429,self.cooldown)
   for scope in scopes:
    row=d.execute('SELECT start,count FROM limits WHERE id=?',(scope,)).fetchone()
    if not row or now-row[0]>=60:d.execute('INSERT OR REPLACE INTO limits VALUES(?,?,1,0)',(scope,now))
    else:d.execute('UPDATE limits SET count=count+1 WHERE id=?',(scope,))
   d.execute('UPDATE sessions SET count=count+1 WHERE id=?',(self.scope(session),));d.execute('DELETE FROM limits WHERE start<? AND blocked_until<?',(now-86400,now))
 def approval(self,actor,body):
  token=secrets.token_hex(24);digest=self.scope(json.dumps(body,sort_keys=True,separators=(',',':')))
  with self.db() as d:d.execute('INSERT INTO approvals(id,actor,digest,expires) VALUES(?,?,?,?)',(self.scope(token),self.scope(actor),digest,self.clock()+60))
  return token
 def use_approval(self,actor,body,token):
  digest=self.scope(json.dumps(body,sort_keys=True,separators=(',',':')))
  with self.db() as d:
   n=d.execute('UPDATE approvals SET used=1 WHERE id=? AND actor=? AND digest=? AND expires>? AND used=0',(self.scope(token),self.scope(actor),digest,self.clock())).rowcount
   require(n==1,'approval_required',403)
 def audit(self,skill,status,error,latency):
  # Only fixed technical enums. Never command, host, path, identity or output.
  with self.db() as d:
   d.execute('BEGIN IMMEDIATE');prev=d.execute('SELECT record_hash FROM audit ORDER BY id DESC LIMIT 1').fetchone();prev=prev[0] if prev else '0'*64
   record=(self.clock(),skill,status,error,int(latency));digest=self.scope(json.dumps(record,separators=(',',':')),prev)
   d.execute('INSERT INTO audit(timestamp,skill_id,status,error_type,latency_ms,previous_hash,record_hash) VALUES(?,?,?,?,?,?,?)',(*record,prev,digest))
 def verify(self):
  prev='0'*64
  with self.db() as d:
   for t,s,st,e,l,p,h in d.execute('SELECT timestamp,skill_id,status,error_type,latency_ms,previous_hash,record_hash FROM audit ORDER BY id'):
    if p!=prev or not hmac.compare_digest(h,self.scope(json.dumps((t,s,st,e,l),separators=(',',':')),prev)):return False
    prev=h
  return True
 def metrics(self):
  lines=['# TYPE harmonika_ssh_commands_total counter','# TYPE harmonika_ssh_command_duration_seconds histogram']
  with self.db() as d:
   for skill,status,count,total in d.execute("SELECT skill_id,status,COUNT(*),SUM(latency_ms)/1000.0 FROM audit WHERE status!='started' AND skill_id!='ssh.api' GROUP BY skill_id,status"):
    labels='skill="'+skill+'",status="'+status+'"';lines.extend([f'harmonika_ssh_commands_total{{{labels}}} {count}',f'harmonika_ssh_command_duration_seconds_sum{{{labels}}} {total}',f'harmonika_ssh_command_duration_seconds_count{{{labels}}} {count}'])
    for bound in (.1,.5,1,5,10,30,120,float('inf')):
     n=d.execute('SELECT COUNT(*) FROM audit WHERE skill_id=? AND status=? AND latency_ms<=?',(skill,status,bound*1000)).fetchone()[0]
     lines.append(f'harmonika_ssh_command_duration_seconds_bucket{{{labels},le="{str(bound) if bound!=float("inf") else "+Inf"}"}} {n}')
   blocked=d.execute("SELECT COUNT(*) FROM audit WHERE error_type IN ('ssh_command_not_allowed','approval_required','ssh_rate_limited','session_quota_exceeded')").fetchone()[0]
  lines+=['# TYPE harmonika_ssh_blocked_total counter',f'harmonika_ssh_blocked_total {blocked}']
  return '\n'.join(lines)+'\n'
