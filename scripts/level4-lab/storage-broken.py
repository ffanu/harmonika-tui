import sqlite3
from harmonika_lab_contracts import validate_label
def connect(path=':memory:'):
 c=sqlite3.connect(path);c.execute('PRAGMA journal_mode=WAL');c.execute('CREATE TABLE IF NOT EXISTS items(id INTEGER PRIMARY KEY AUTOINCREMENT,request_id TEXT UNIQUE NOT NULL,label TEXT NOT NULL,qty INTEGER NOT NULL)');c.commit();return c
def items(c):return [dict(zip(['id','request_id','label','qty'],x)) for x in c.execute('SELECT id,request_id,label,qty FROM items ORDER BY id')]
def put(c,r):
 label=validate_label(r['label']);qty=int(r['qty']);cur=c.execute('INSERT OR REPLACE INTO items(request_id,label,qty) VALUES(?,?,?)',(r['request_id'],label,qty));c.commit();return 201,{'item':dict(id=cur.lastrowid,request_id=r['request_id'],label=label,qty=qty),'replayed':False}
