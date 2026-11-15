"""Protected release-scoped synthetic mechanics; no payloads or raw errors."""
import hashlib,os,sqlite3,time
from pathlib import Path
ROOT=Path(os.environ.get('LAB_DATA','/data'))
def connect():
 c=sqlite3.connect(ROOT/'mechanics.sqlite',timeout=.25);c.row_factory=sqlite3.Row;c.execute('PRAGMA journal_mode=WAL')
 c.executescript("""CREATE TABLE IF NOT EXISTS calls(id INTEGER PRIMARY KEY AUTOINCREMENT,kind TEXT,owner TEXT,key_sha256 TEXT,job_id TEXT,status INTEGER,at REAL,release TEXT NOT NULL DEFAULT 'legacy');CREATE TABLE IF NOT EXISTS spent(kind TEXT PRIMARY KEY,n INTEGER);CREATE TABLE IF NOT EXISTS targets(kind TEXT PRIMARY KEY,owner TEXT,key_sha256 TEXT,job_id TEXT);""")
 c.execute('BEGIN IMMEDIATE')
 if 'release' not in {r[1] for r in c.execute('PRAGMA table_info(calls)')}:c.execute("ALTER TABLE calls ADD COLUMN release TEXT NOT NULL DEFAULT 'legacy'")
 c.commit();return c
def scoped(kind):return kind+'@'+os.environ.get('LAB_RELEASE','local')
def digest(owner,key):return hashlib.sha256((owner+'\0'+key).encode()).hexdigest()
def audit(kind,owner='',key='',job_id=None,status=0):
 with connect() as c:c.execute('INSERT INTO calls(kind,owner,key_sha256,job_id,status,at,release) VALUES(?,?,?,?,?,?,?)',(kind,owner,digest(owner,key) if key else '',job_id,status,time.time(),os.environ.get('LAB_RELEASE','local')))
def take(kind,owner='',key='',job_id=None,limit=1):
 if not (ROOT/'faults'/kind).exists():return False
 kind=scoped(kind)
 with connect() as c:
  c.execute('BEGIN IMMEDIATE');row=c.execute('SELECT n FROM spent WHERE kind=?',(kind,)).fetchone();n=row[0] if row else 0
  if n>=limit:return False
  c.execute('INSERT INTO spent VALUES(?,1) ON CONFLICT(kind) DO UPDATE SET n=n+1',(kind,))
  if key:c.execute('INSERT OR IGNORE INTO targets VALUES(?,?,?,?)',(kind,owner,digest(owner,key),job_id))
 return True
def lookup_fault(owner,key):
 if not (ROOT/'faults/child-lookup-fail-two').exists():return False
 with connect() as c:target=c.execute('SELECT * FROM targets WHERE kind=?',(scoped('child-submit-ack-loss'),)).fetchone()
 if not target or target['owner']!=owner or target['key_sha256']!=digest(owner,key):return False
 return take('child-lookup-fail-two',limit=2)
