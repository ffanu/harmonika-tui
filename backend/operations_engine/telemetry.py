"""Bounded WAL telemetry. Fixed enums, opaque actor HMAC, numeric mechanics only."""
import hashlib,hmac,sqlite3,time
from contextlib import closing
from .common import private_path
STATUSES={'completed','passed','failed','partial','degraded','needs_toolchain','blocked','preview','verified','rolled_back','rollback_failed','unknown','accepted','running','queued','waiting','prepared','succeeded','inconclusive'}
ERRORS={'none','invalid_input','access_denied','unavailable','timeout','operation_failed','unknown_outcome'}
class Telemetry:
 def __init__(self,root,key,clock=time.time):
  self.root=private_path(root,directory=True,create=True);self.path=private_path(self.root/'mechanics.sqlite3',create=True);self.key=key;self.clock=clock
  with closing(sqlite3.connect(self.path,timeout=.5)) as c:
   c.execute('PRAGMA journal_mode=WAL');c.execute('CREATE TABLE IF NOT EXISTS events(ts INTEGER, actor TEXT, skill TEXT, status TEXT, latency_ms REAL, error TEXT)');c.commit()
 def record(self,actor,skill,status,latency,error='none'):
  from .catalog import IDS
  if skill not in IDS:return
  status=status if status in STATUSES else 'failed';error=error if error in ERRORS else 'operation_failed'
  actor=hmac.new(self.key,actor.encode(),hashlib.sha256).hexdigest()
  with closing(sqlite3.connect(self.path,timeout=.5)) as c,c:
   c.execute('INSERT INTO events VALUES(?,?,?,?,?,?)',(int(self.clock()),actor,skill,status,min(600000,max(0,latency)),error))
   c.execute('DELETE FROM events WHERE rowid NOT IN(SELECT rowid FROM events ORDER BY rowid DESC LIMIT 10000) OR ts<?',(int(self.clock())-604800,))
 def summary(self,actor):
  ref=hmac.new(self.key,actor.encode(),hashlib.sha256).hexdigest()
  with closing(sqlite3.connect(self.path,timeout=.5)) as c:
   rows=c.execute('SELECT skill,status,count(*),round(avg(latency_ms),2) FROM events WHERE actor=? GROUP BY skill,status ORDER BY skill,status',(ref,)).fetchall()
  return [{'skill_id':r[0],'status':r[1],'count':r[2],'average_latency_ms':r[3]} for r in rows]
