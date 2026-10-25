import asyncio
from contextlib import closing
import hashlib
import io
import ipaddress
import json
import os
from pathlib import Path
import re
import sqlite3
import time
from urllib.parse import urlsplit
from uuid import uuid4
from PIL import Image,UnidentifiedImageError

class RTXError(ValueError):
    def __init__(self,code,status=400,uncertain=False):super().__init__(code);self.code=code;self.status=status;self.uncertain=uncertain

ACTIVE=('dispatching','running','unknown','cancel_requested')
TERMINAL=('completed','failed','cancelled')
FILE_LIMIT=10*1024*1024
RESULT_LIMIT=16*1024*1024

def safe_id(value,prefix=''):
    return isinstance(value,str) and re.fullmatch(re.escape(prefix)+r'[A-Za-z0-9_-]{1,128}',value) is not None

def image_info(data,max_bytes=RESULT_LIMIT):
    if not data or len(data)>max_bytes:raise RTXError('file_too_large',413)
    magic=(data.startswith(b'\x89PNG\r\n\x1a\n') or data.startswith(b'\xff\xd8\xff') or (data.startswith(b'RIFF') and data[8:12]==b'WEBP'))
    if not magic:raise RTXError('unsupported_image',415)
    try:
        with Image.open(io.BytesIO(data)) as image:
            if image.format not in {'PNG','JPEG','WEBP'}:raise RTXError('unsupported_image',415)
            width,height=image.size
            if width<1 or height<1 or width>4096 or height>4096 or width*height>16777216:raise RTXError('image_dimensions_exceeded',413)
            if getattr(image,'is_animated',False) or getattr(image,'n_frames',1)>1:raise RTXError('animated_image_unsupported',415)
            fmt=image.format;image.verify()
        with Image.open(io.BytesIO(data)) as decoded:decoded.load()
    except RTXError:raise
    except (UnidentifiedImageError,OSError,ValueError,SyntaxError,Image.DecompressionBombError):raise RTXError('corrupt_image',422) from None
    return {'mime_type':{'PNG':'image/png','JPEG':'image/jpeg','WEBP':'image/webp'}[fmt],'width':width,'height':height,'size_bytes':len(data),'sha256':hashlib.sha256(data).hexdigest()}

def validate_endpoint(value,mode):
    try:
        parsed=urlsplit(value);address=ipaddress.ip_address(parsed.hostname or '')
        if parsed.scheme not in {'http','https'} or not (address.is_private or address.is_loopback) or address.is_unspecified or address.is_multicast or address.is_link_local:raise ValueError()
        if parsed.username or parsed.password or parsed.query or parsed.fragment or (parsed.port is not None and not 1<=parsed.port<=65535):raise ValueError()
        if mode=='legacy' and not address.is_loopback:raise ValueError()
    except (ValueError,TypeError):raise RTXError('invalid_worker_endpoint') from None
    return value.rstrip('/')

class Store:
    def __init__(self,root,max_pending=64,max_owner_pending=8):
        self.root=Path(root).absolute()
        if any(p.is_symlink() for p in [self.root,*self.root.parents]):raise RTXError('unsafe_storage_path')
        self.root.mkdir(parents=True,exist_ok=True,mode=0o700);self.root.chmod(0o700)
        self.files=self.root/'files';self.files.mkdir(exist_ok=True,mode=0o700)
        if self.files.is_symlink():raise RTXError('unsafe_storage_path')
        self.db=self.root/'queue.sqlite3';self.max_pending=max_pending;self.max_owner_pending=max_owner_pending
        if self.db.is_symlink():raise RTXError('unsafe_storage_path')
        with closing(self.connect()) as c,c:
            c.execute('PRAGMA journal_mode=WAL')
            c.execute('CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY, owner TEXT NOT NULL, kind TEXT NOT NULL, payload TEXT NOT NULL, digest TEXT NOT NULL, idem TEXT NOT NULL,status TEXT NOT NULL,error_code TEXT,result TEXT,created REAL NOT NULL,updated REAL NOT NULL,deadline REAL NOT NULL,cancel_requested INTEGER NOT NULL DEFAULT 0,UNIQUE(owner,idem))')
            c.execute('CREATE TABLE IF NOT EXISTS files(id TEXT PRIMARY KEY,owner TEXT NOT NULL,name TEXT NOT NULL,meta TEXT NOT NULL,size INTEGER NOT NULL,created REAL NOT NULL)')
            c.execute('CREATE INDEX IF NOT EXISTS job_status_created ON jobs(status,created)')
        self.db.chmod(0o600)
    def connect(self):
        c=sqlite3.connect(self.db,timeout=2);c.row_factory=sqlite3.Row
        return c
    async def call(self,name,*args):return await asyncio.to_thread(getattr(self,name),*args)
    def put_file(self,owner,name,data,limit=FILE_LIMIT):
        meta=image_info(data,limit);fid='file_'+uuid4().hex
        name=re.sub(r'[^A-Za-z0-9 ._-]','_',str(name).replace('\\','/').split('/')[-1])[:100] or 'image'
        file=self.files/fid
        fd=os.open(file,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        try:
            with os.fdopen(fd,'wb') as stream:stream.write(data)
            with closing(self.connect()) as c,c:
                c.execute('BEGIN IMMEDIATE')
                count,total=c.execute('SELECT count(*),coalesce(sum(size),0) FROM files').fetchone()
                if count>=1000 or total+len(data)>1024*1024*1024:raise RTXError('storage_quota_exceeded',507)
                c.execute('INSERT INTO files VALUES(?,?,?,?,?,?)',(fid,owner,name,json.dumps(meta),len(data),time.time()))
        except BaseException:
            file.unlink(missing_ok=True);raise
        return {'file_id':fid,'name':name,**meta,'status':'ready'}
    def file(self,owner,fid):
        if not safe_id(fid,'file_'):raise RTXError('file_not_found',404)
        with closing(self.connect()) as c:row=c.execute('SELECT * FROM files WHERE id=? AND owner=?',(fid,owner)).fetchone()
        file=self.files/fid
        if row is None or file.is_symlink() or not file.is_file():raise RTXError('file_not_found',404)
        return {'file_id':fid,'name':row['name'],**json.loads(row['meta']),'status':'ready'}
    def file_bytes(self,owner,fid):
        meta=self.file(owner,fid);data=(self.files/fid).read_bytes()
        if len(data)!=meta['size_bytes'] or hashlib.sha256(data).hexdigest()!=meta['sha256']:raise RTXError('file_integrity_failed',422)
        return data,meta
    def submit(self,owner,payload,idem,ttl=900):
        if not safe_id(idem):raise RTXError('invalid_idempotency_key')
        if not isinstance(payload,dict) or set(payload)-{'kind','prompt','reference_file_ids','size'}:raise RTXError('invalid_payload')
        kind=payload.get('kind');prompt=payload.get('prompt','');refs=payload.get('reference_file_ids',[]);size=payload.get('size','768x768')
        if not isinstance(kind,str) or kind not in {'generate','analyze'}:raise RTXError('invalid_kind')
        if not isinstance(prompt,str) or not prompt.strip() or len(prompt.encode())>12000:raise RTXError('invalid_prompt')
        if not isinstance(refs,list) or len(refs)>3 or any(not isinstance(r,str) for r in refs) or len(refs)!=len(set(refs)):raise RTXError('invalid_references')
        if kind=='analyze' and not refs:raise RTXError('references_required')
        if not isinstance(size,str) or size not in {'512x512','768x768','1024x1024'}:raise RTXError('invalid_size')
        for fid in refs:self.file(owner,fid)
        payload={'kind':kind,'prompt':prompt.strip(),'reference_file_ids':refs,'size':size}
        raw=json.dumps(payload,sort_keys=True,separators=(',',':'));digest=hashlib.sha256(raw.encode()).hexdigest();now=time.time()
        with closing(self.connect()) as c,c:
            c.execute('BEGIN IMMEDIATE')
            row=c.execute('SELECT * FROM jobs WHERE owner=? AND idem=?',(owner,idem)).fetchone()
            if row:
                if row['digest']!=digest:raise RTXError('idempotency_conflict',409)
                return self.public(row),False
            total=c.execute('SELECT count(*) FROM jobs').fetchone()[0]
            pending=c.execute("SELECT count(*) FROM jobs WHERE status NOT IN ('completed','failed','cancelled')").fetchone()[0]
            own=c.execute("SELECT count(*) FROM jobs WHERE owner=? AND status NOT IN ('completed','failed','cancelled')",(owner,)).fetchone()[0]
            if total>=10000:raise RTXError('history_quota_exceeded',429)
            if pending>=self.max_pending or own>=self.max_owner_pending:raise RTXError('queue_full',429)
            jid='job_'+uuid4().hex
            c.execute('INSERT INTO jobs(id,owner,kind,payload,digest,idem,status,created,updated,deadline) VALUES(?,?,?,?,?,?,?,?,?,?)',(jid,owner,kind,raw,digest,idem,'queued',now,now,now+ttl))
            row=c.execute('SELECT * FROM jobs WHERE id=?',(jid,)).fetchone()
        return self.public(row),True
    @staticmethod
    def public(row):
        result={'job_id':row['id'],'kind':row['kind'],'status':row['status'],'created_at':row['created'],'updated_at':row['updated'],'cancel_requested':bool(row['cancel_requested'])}
        if row['error_code']:result['error']={'code':row['error_code']}
        if row['result']:result['result']=json.loads(row['result'])
        return result
    def get(self,owner,jid,internal=False):
        with closing(self.connect()) as c:row=c.execute('SELECT * FROM jobs WHERE id=? AND owner=?',(jid,owner)).fetchone()
        if row is None:raise RTXError('job_not_found',404)
        return dict(row) if internal else self.public(row)
    def claim(self):
        now=time.time()
        with closing(self.connect()) as c,c:
            c.execute('BEGIN IMMEDIATE')
            c.execute("UPDATE jobs SET status='failed',error_code='queue_deadline_exceeded',updated=? WHERE status='queued' AND deadline<?",(now,now))
            if c.execute("SELECT 1 FROM jobs WHERE status IN ('dispatching','running','unknown','cancel_requested') LIMIT 1").fetchone():return None
            row=c.execute("SELECT * FROM jobs WHERE status='queued' ORDER BY created,id LIMIT 1").fetchone()
            if row is None:return None
            c.execute("UPDATE jobs SET status='dispatching',updated=? WHERE id=?",(now,row['id']))
            return dict(row)
    def startup(self):
        with closing(self.connect()) as c,c:c.execute("UPDATE jobs SET status='unknown',error_code='service_interrupted',updated=? WHERE status IN ('dispatching','running','cancel_requested')",(time.time(),))
    def begin_remote(self,jid):
        with closing(self.connect()) as c,c:
            c.execute('BEGIN IMMEDIATE');row=c.execute('SELECT status,cancel_requested FROM jobs WHERE id=?',(jid,)).fetchone()
            if row is None or row['status'] in TERMINAL:return False
            if row['cancel_requested']:
                c.execute("UPDATE jobs SET status='cancelled',updated=? WHERE id=?",(time.time(),jid));return False
            c.execute("UPDATE jobs SET status='running',updated=? WHERE id=?",(time.time(),jid));return True
    def set_state(self,jid,status,error=None,result=None):
        if status not in {'running','completed','failed','cancelled','unknown'}:raise RTXError('invalid_state')
        with closing(self.connect()) as c,c:
            c.execute('BEGIN IMMEDIATE');row=c.execute('SELECT status,cancel_requested FROM jobs WHERE id=?',(jid,)).fetchone()
            if row is None or row['status'] in TERMINAL:return
            if row['cancel_requested'] and status=='completed':status='cancelled';result=None
            c.execute('UPDATE jobs SET status=?,error_code=?,result=?,updated=? WHERE id=?',(status,error,None if result is None else json.dumps(result),time.time(),jid))
    def cancel(self,owner,jid):
        with closing(self.connect()) as c,c:
            c.execute('BEGIN IMMEDIATE');row=c.execute('SELECT * FROM jobs WHERE id=? AND owner=?',(jid,owner)).fetchone()
            if row is None:raise RTXError('job_not_found',404)
            if row['status'] not in TERMINAL:
                status='cancelled' if row['status']=='queued' else 'cancel_requested'
                c.execute('UPDATE jobs SET status=?,cancel_requested=1,updated=? WHERE id=?',(status,time.time(),jid))
            row=c.execute('SELECT * FROM jobs WHERE id=?',(jid,)).fetchone()
        return self.public(row)
    def uncertain(self):
        with closing(self.connect()) as c:return c.execute("SELECT 1 FROM jobs WHERE status IN ('unknown','cancel_requested') LIMIT 1").fetchone() is not None
    def counts(self,owner):
        with closing(self.connect()) as c:return {r[0]:r[1] for r in c.execute('SELECT status,count(*) FROM jobs WHERE owner=? GROUP BY status',(owner,))}
