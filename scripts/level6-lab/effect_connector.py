"""Immutable synthetic external sink; independent commit and deliberate ACK faults."""
import base64,hashlib,json,os,sqlite3,time
from pathlib import Path
class OutcomeUnknown(Exception):pass
class EffectConnector:
 def __init__(self,root):
  self.root=Path(root);self.root.mkdir(parents=True,exist_ok=True)
  with self.db() as c:
   c.executescript('CREATE TABLE IF NOT EXISTS effects(job_id TEXT PRIMARY KEY, sha256 TEXT NOT NULL, content BLOB NOT NULL); CREATE TABLE IF NOT EXISTS calls(id INTEGER PRIMARY KEY,job_id TEXT,kind TEXT); CREATE TABLE IF NOT EXISTS spent(kind TEXT PRIMARY KEY);')
 def db(self):
  c=sqlite3.connect(str(self.root/'sink.sqlite'),timeout=2);c.execute('PRAGMA journal_mode=WAL');return c
 def lookup(self,job_id):
  with self.db() as c:
   c.execute('INSERT INTO calls(job_id,kind) VALUES(?,?)',(job_id,'lookup'))
   row=c.execute('SELECT sha256,content FROM effects WHERE job_id=?',(job_id,)).fetchone()
  return None if row is None else {'sha256':row[0],'content_b64':base64.b64encode(row[1]).decode()}
 def execute(self,job_id,payload):
  with self.db() as c:c.execute('INSERT INTO calls(job_id,kind) VALUES(?,?)',(job_id,'execute'))
  while (self.root/'hold').exists():time.sleep(.05)
  content=json.dumps(payload,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode();digest=hashlib.sha256(content).hexdigest();fault=None
  with self.db() as c:
   c.execute('BEGIN IMMEDIATE');row=c.execute('SELECT sha256,content FROM effects WHERE job_id=?',(job_id,)).fetchone()
   if row is None:c.execute('INSERT INTO effects VALUES(?,?,?)',(job_id,digest,content))
   elif row[0]!=digest:raise ValueError('effect_conflict')
   for kind in ['crash-after-effect','unknown-ack']:
    if (self.root/kind).exists() and not c.execute('SELECT 1 FROM spent WHERE kind=?',(kind,)).fetchone():
     c.execute('INSERT INTO spent VALUES(?)',(kind,));c.execute('INSERT INTO calls(job_id,kind) VALUES(?,?)',(job_id,kind));fault=kind;break
  if fault=='crash-after-effect':os._exit(42)
  if fault=='unknown-ack':raise OutcomeUnknown('outcome_unknown')
  return {'sha256':digest,'content_b64':base64.b64encode(content).decode()}
