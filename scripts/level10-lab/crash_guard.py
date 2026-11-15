"""Registered container main-process crash boundary, once per role/generation."""
import json,os,sqlite3,threading,time
from contextlib import closing
from pathlib import Path
def start():
 role=os.environ['WORKER_ID'];release=os.environ['LAB_RELEASE'];root=Path('/data')
 def watch():
  seen=None
  while True:
   try:
    marker=root/'crash-generation.json'
    if marker.exists():
     generation=json.loads(marker.read_text())['generation']
     if generation==seen:time.sleep(.05);continue
     with closing(sqlite3.connect(root/'chaos.sqlite',timeout=.5)) as c:
      c.execute('PRAGMA journal_mode=WAL');c.execute('CREATE TABLE IF NOT EXISTS crashes(generation TEXT,role TEXT,release TEXT,at REAL,PRIMARY KEY(generation,role))');c.execute('BEGIN IMMEDIATE')
      row=c.execute('INSERT OR IGNORE INTO crashes VALUES(?,?,?,?)',(generation,role,release,time.time()))
      won=row.rowcount==1;c.commit()
     if won:os._exit(42)
     seen=generation
   except (OSError,ValueError,sqlite3.Error,KeyError):pass
   time.sleep(.05)
 threading.Thread(target=watch,daemon=True).start()
