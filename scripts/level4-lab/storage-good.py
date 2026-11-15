"""Trusted old release used to seed the isolated staging lab."""
import sqlite3
from harmonika_lab_contracts import validate_label

def connect(path=':memory:'):
    c=sqlite3.connect(path,timeout=5)
    c.execute('PRAGMA journal_mode=WAL')
    c.execute('CREATE TABLE IF NOT EXISTS items(id INTEGER PRIMARY KEY AUTOINCREMENT,request_id TEXT UNIQUE NOT NULL,label TEXT NOT NULL,qty INTEGER NOT NULL)')
    c.commit();return c

def items(c):
    return [dict(zip(['id','request_id','label','qty'],v)) for v in c.execute('SELECT id,request_id,label,qty FROM items ORDER BY id')]

def put(c,record):
    if not isinstance(record,dict):raise ValueError()
    key=record.get('request_id');label=validate_label(record.get('label'));qty=record.get('qty')
    if not isinstance(key,str) or not key.strip() or type(qty) is not int or qty<0:raise ValueError()
    c.execute('BEGIN IMMEDIATE')
    try:
        old=c.execute('SELECT id,request_id,label,qty FROM items WHERE request_id=?',(key,)).fetchone()
        if old:
            if old[2:]!=(label,qty):c.rollback();return 409,{'error':'idempotency_conflict'}
            c.commit();return 200,{'item':dict(zip(['id','request_id','label','qty'],old)),'replayed':True}
        cur=c.execute('INSERT INTO items(request_id,label,qty) VALUES(?,?,?)',(key,label,qty));c.commit()
        return 201,{'item':dict(id=cur.lastrowid,request_id=key,label=label,qty=qty),'replayed':False}
    except Exception:c.rollback();raise
