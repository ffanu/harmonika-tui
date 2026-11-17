"""Durable mechanical jobs. A stale lease is uncertainty, never replay authority."""
from contextlib import contextmanager
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import time
from uuid import uuid4

STATES = frozenset(('prepared','running','succeeded','failed','unknown','rolled_back','cancelled'))
TERMINAL = frozenset(('succeeded','failed','rolled_back','cancelled'))
DIGEST_FIELDS = frozenset(('plan_sha256','artifact_sha256','previous_release_sha256','release_sha256',
                          'previous_ref_sha256','release_ref_sha256','health_body_sha256','config_sha256','deployment_ref_sha256'))
INTEGER_FIELDS = frozenset(('health_status','files','bytes','attempt','latency_ms'))
BOOLEAN_FIELDS = frozenset(('health_verified','rollback_verified','effect_observed','lease_expired','rollback_attempted'))
PHASES = frozenset(('prepared','copy_started','staged','switch_started','switched','health_checked',
                    'rollback_started','rolled_back','reconciled','verified','failed','unknown','effect_unchanged'))
ERRORS = frozenset(('health_check_failed','rollback_unverified','lease_expired','artifact_changed','pointer_changed',
                    'deployment_interrupted','execution_unknown','verification_failed','effect_unchanged','deploy_failed'))

class JobError(ValueError):
    def __init__(self, code): self.code=code;super().__init__(code)

def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=True).encode()).hexdigest()

def key_bytes(key):
    if isinstance(key,str) and re.fullmatch('[a-fA-F0-9]{64}',key):key=bytes.fromhex(key)
    if not isinstance(key,bytes) or not 32<=len(key)<=128:raise JobError('job_key_invalid')
    return key

def linklike(path):
    try:s=Path(path).lstat()
    except FileNotFoundError:return False
    return stat.S_ISLNK(s.st_mode) or bool(getattr(s,'st_file_attributes',0)&0x400)

def safe_root(root):
    p=Path(root)
    if not p.is_absolute() or '..' in p.parts or any(linklike(q) for q in (p,*p.parents)):raise JobError('job_root_unsafe')
    p.mkdir(parents=True,exist_ok=True,mode=0o700)
    s=p.stat()
    if not stat.S_ISDIR(s.st_mode) or os.name=='posix' and (s.st_uid!=os.getuid() or s.st_mode&0o077):raise JobError('job_root_unsafe')
    return p.resolve()

def evidence(value):
    if value is None:return {}
    if type(value) is not dict or len(value)>24:raise JobError('job_evidence_invalid')
    for k,v in value.items():
        if k in DIGEST_FIELDS:
            if not isinstance(v,str) or not re.fullmatch('[a-f0-9]{64}',v):raise JobError('job_evidence_invalid')
        elif k in INTEGER_FIELDS:
            if type(v) is not int or not 0<=v<=2**53-1:raise JobError('job_evidence_invalid')
        elif k in BOOLEAN_FIELDS:
            if type(v) is not bool:raise JobError('job_evidence_invalid')
        elif k in ('phase','last_phase'):
            if not isinstance(v,str) or v not in PHASES:raise JobError('job_evidence_invalid')
        elif k=='error_type':
            if not isinstance(v,str) or v not in ERRORS:raise JobError('job_evidence_invalid')
        else:raise JobError('job_evidence_invalid')
    return dict(value)

class JobStore:
    def __init__(self,root,key,*,clock=time.time):
        self.root=safe_root(root);self.key=key_bytes(key);self.clock=clock;self.path=self.root/'jobs.sqlite'
        if linklike(self.path):raise JobError('job_database_unsafe')
        fd=os.open(self.path,os.O_CREAT|os.O_RDWR|getattr(os,'O_NOFOLLOW',0),0o600)
        try:
            s=os.fstat(fd)
            if not stat.S_ISREG(s.st_mode) or s.st_nlink!=1 or os.name=='posix' and (s.st_uid!=os.getuid() or s.st_mode&0o077):raise JobError('job_database_unsafe')
        finally:os.close(fd)
        with self._db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY,actor TEXT NOT NULL,project TEXT NOT NULL,kind TEXT NOT NULL,idempotency TEXT NOT NULL,request_digest TEXT NOT NULL,state TEXT NOT NULL,version INTEGER NOT NULL,created REAL NOT NULL,updated REAL NOT NULL,lease_until REAL,lease_owner TEXT,evidence TEXT NOT NULL,UNIQUE(actor,project,kind,idempotency))')

    def ref(self,label,value):
        if not isinstance(value,str) or not value or len(value)>4096:raise JobError('job_identity_invalid')
        return hmac.new(self.key,json.dumps([label,value],ensure_ascii=True,separators=(',',':')).encode(),hashlib.sha256).hexdigest()

    @contextmanager
    def _db(self):
        if linklike(self.path) or any(linklike(Path(str(self.path)+suffix)) for suffix in ('-wal','-shm')):raise JobError('job_database_unsafe')
        db=sqlite3.connect(self.path,timeout=10,isolation_level=None);db.row_factory=sqlite3.Row
        try:
            db.execute('PRAGMA busy_timeout=10000');db.execute('PRAGMA journal_mode=WAL');db.execute('PRAGMA synchronous=FULL')
            db.execute('BEGIN IMMEDIATE')
            yield db
            db.commit()
        except BaseException:db.rollback();raise
        finally:db.close()

    def _ref(self,row):
        mac=self.ref('job-ref',digest([row['id'],row['actor'],row['project'],row['kind']]))
        return 'opj_'+row['id']+'.'+mac

    def _owned(self,db,actor,project,job):
        if not isinstance(job,str) or not re.fullmatch(r'opj_[a-f0-9]{32}\.[a-f0-9]{64}',job):raise JobError('job_not_found')
        row=db.execute('SELECT * FROM jobs WHERE id=?',(job[4:36],)).fetchone()
        if row is None or not hmac.compare_digest(row['actor'],self.ref('actor',actor)) or not hmac.compare_digest(row['project'],self.ref('project',project)) or not hmac.compare_digest(self._ref(row),job):raise JobError('job_not_found')
        return row

    def _dto(self,row):
        return {'job_ref':self._ref(row),'state':row['state'],'version':row['version'],'kind':row['kind'],
                'created_at':row['created'],'updated_at':row['updated'],'lease_expires_at':row['lease_until'],
                'request_digest':row['request_digest'],'evidence':json.loads(row['evidence']),
                'resume_only':row['state']!='prepared','retry_safe':False,'terminal':row['state'] in TERMINAL}

    def _expire(self,db,row):
        if row['state']=='running' and row['lease_until'] is not None and row['lease_until']<=self.clock():
            saved=json.loads(row['evidence'])
            data={**saved,'last_phase':saved.get('phase','prepared'),'phase':'unknown','error_type':'lease_expired','lease_expired':True}
            db.execute("UPDATE jobs SET state='unknown',version=version+1,updated=?,lease_until=NULL,lease_owner=NULL,evidence=? WHERE id=? AND version=?",(self.clock(),json.dumps(data,sort_keys=True),row['id'],row['version']))
            return db.execute('SELECT * FROM jobs WHERE id=?',(row['id'],)).fetchone()
        return row

    def prepare(self,actor,project,kind,idempotency_key,request_digest,*,evidence=None):
        initial=globals()['evidence'](evidence)
        if kind not in ('deployment.apply','deployment.rollback','tool.execution','browser.audit','code.audit','diagnostic','selftest'):raise JobError('job_kind_invalid')
        if not isinstance(request_digest,str) or not re.fullmatch('[a-f0-9]{64}',request_digest):raise JobError('job_request_digest_invalid')
        ah,ph,ih=self.ref('actor',actor),self.ref('project',project),self.ref('idempotency',idempotency_key)
        with self._db() as db:
            row=db.execute('SELECT * FROM jobs WHERE actor=? AND project=? AND kind=? AND idempotency=?',(ah,ph,kind,ih)).fetchone()
            if row:
                if not hmac.compare_digest(row['request_digest'],request_digest):raise JobError('job_idempotency_conflict')
                return self._dto(self._expire(db,row))
            # A replacement request key is not replay authority for the same
            # in-flight/uncertain mechanical request. Keep its original job.
            unresolved=db.execute("SELECT * FROM jobs WHERE actor=? AND project=? AND kind=? AND request_digest=? AND state IN ('prepared','running','unknown') ORDER BY created LIMIT 1",(ah,ph,kind,request_digest)).fetchone()
            if unresolved:return self._dto(self._expire(db,unresolved))
            now=self.clock();identity=uuid4().hex
            db.execute('INSERT INTO jobs VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',(identity,ah,ph,kind,ih,request_digest,'prepared',1,now,now,None,None,json.dumps(initial,sort_keys=True)))
            return self._dto(db.execute('SELECT * FROM jobs WHERE id=?',(identity,)).fetchone())

    def get(self,actor,project,job):return self.resume(actor,project,job)

    def resume(self,actor,project,job):
        with self._db() as db:return self._dto(self._expire(db,self._owned(db,actor,project,job)))

    def transition(self,actor,project,job,expected_version,state,evidence=None,*,lease_owner=None,lease_seconds=120):
        data=globals()['evidence'](evidence)
        if not isinstance(state,str) or state not in STATES or type(expected_version) is not int:raise JobError('job_transition_invalid')
        if type(lease_seconds) is not int or not 1<=lease_seconds<=600:raise JobError('job_lease_invalid')
        with self._db() as db:
            row=self._owned(db,actor,project,job)
            # Commit expiration as its own observation via resume before a write.
            if row['state']=='running' and row['lease_until']<=self.clock():raise JobError('job_lease_expired')
            if row['version']!=expected_version:raise JobError('job_version_conflict')
            allowed={'prepared':{'running','cancelled'},'running':{'running','succeeded','failed','unknown','rolled_back'}}
            if state not in allowed.get(row['state'],set()):raise JobError('job_transition_invalid')
            owner=self.ref('lease',lease_owner) if lease_owner else None
            if state=='running' and row['state']=='prepared' and owner is None:raise JobError('job_lease_required')
            if row['state']=='running' and (owner is None or not hmac.compare_digest(owner,row['lease_owner'] or '')):raise JobError('job_lease_mismatch')
            merged={**json.loads(row['evidence']),**data}
            db.execute('UPDATE jobs SET state=?,version=version+1,updated=?,lease_until=?,lease_owner=?,evidence=? WHERE id=? AND version=?',(state,self.clock(),self.clock()+lease_seconds if state=='running' else None,owner if state=='running' else None,json.dumps(merged,sort_keys=True),row['id'],expected_version))
            return self._dto(db.execute('SELECT * FROM jobs WHERE id=?',(row['id'],)).fetchone())

    def _deployment_blocked(self,deployment_ref_sha256,exclude_job):
        with self._db() as db:
            for row in db.execute("SELECT * FROM jobs WHERE kind IN ('deployment.apply','deployment.rollback') AND state IN ('running','unknown')"):
                if self._ref(row)!=exclude_job and json.loads(row['evidence']).get('deployment_ref_sha256')==deployment_ref_sha256:return True
        return False

    def reconcile(self,actor,project,job,expected_version,observed_state,evidence):
        return self._reconcile(actor,project,job,expected_version,observed_state,evidence,False)

    def _reconcile_deployment(self,actor,project,job,expected_version,observed_state,evidence):
        return self._reconcile(actor,project,job,expected_version,observed_state,evidence,True)

    def _reconcile(self,actor,project,job,expected_version,observed_state,evidence,deployment):
        data=globals()['evidence'](evidence)
        if observed_state not in ('succeeded','failed','rolled_back') or data.get('effect_observed') is not True:raise JobError('job_reconciliation_invalid')
        if observed_state in ('succeeded','rolled_back') and data.get('health_verified') is not True:raise JobError('job_reconciliation_invalid')
        with self._db() as db:
            row=self._owned(db,actor,project,job)
            if row['version']!=expected_version:raise JobError('job_version_conflict')
            if row['kind'].startswith('deployment.') and not deployment:raise JobError('job_engine_reconciliation_required')
            if row['state']!='unknown':raise JobError('job_reconciliation_invalid')
            merged={**json.loads(row['evidence']),**data,'phase':'reconciled'}
            db.execute('UPDATE jobs SET state=?,version=version+1,updated=?,evidence=? WHERE id=? AND version=?',(observed_state,self.clock(),json.dumps(merged,sort_keys=True),row['id'],expected_version))
            return self._dto(db.execute('SELECT * FROM jobs WHERE id=?',(row['id'],)).fetchone())
