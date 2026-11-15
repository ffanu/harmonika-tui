"""SQLite WAL mechanics and AES-GCM consented samples with bounded retention."""
import hashlib
import hmac
import json
import os
import re
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from .redaction import sanitize

KINDS = {'request.completed', 'tool.requested', 'tool.client_reported',
         'tool.server_executed', 'artifact.created', 'search.executed',
         'feedback.recorded', 'job.status', 'artifact.reported'}
ERRORS = {'none', 'timeout', 'upstream_unavailable', 'bad_payload', 'invalid_input',
          'access_denied', 'cancelled', 'tool_failed', 'stream_incomplete',
          'capture_truncated', 'unknown_error'}
ORIGINS = {'gateway', 'server_executor', 'client_reported', 'model_requested', 'server_reported'}
CATEGORIES = {'chat', 'image', 'video', 'ocr', 'audio', 'coding', 'search', 'other'}
KNOWN_SKILLS = {'image.generate','image.edit','image.analyze','image.upscale','video.generate',
                'audio.tts','audio.transcribe','web.search','web.fetch','ocr.document',
                'generate_image','analyze_image','ocr_document','read','write','edit','shell',
                'fs.read','fs.write','fs.list','fs.tree','fs.glob','exec.shell','exec.python',
                'exec.node','exec.background','code.read','code.write','code.edit','code.search',
                'code.grep','test.run','git.status','git.diff','git.commit'}


def integer(v, low, high):
    if type(v) is not int or not low <= v <= high:
        raise ValueError('invalid_integer')
    return v


class Store:
    def __init__(self, path, encryption_key, identity_key, *, max_events=100000,
                 max_bytes=512 * 1024 * 1024, retention_days=30, clock=time.time):
        if len(encryption_key) != 32 or len(identity_key) != 32 or encryption_key == identity_key:
            raise ValueError('independent_32byte_keys_required')
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path.parent.chmod(0o700)
        self.aes = AESGCM(encryption_key)
        self.identity_key = identity_key
        self.clock = clock
        self.max_events = max_events
        self.max_bytes = max_bytes
        self.retention = integer(retention_days, 1, 90) * 86400
        with self.connect() as c:
            c.execute('PRAGMA journal_mode=WAL')
            c.executescript('''
            CREATE TABLE IF NOT EXISTS consent(
              key_ref TEXT PRIMARY KEY, granted INTEGER NOT NULL,
              updated_at INTEGER NOT NULL, expires_at INTEGER NOT NULL,
              basis TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS traces(
              trace_id TEXT PRIMARY KEY, key_ref TEXT NOT NULL,
              session_ref TEXT NOT NULL, timestamp INTEGER NOT NULL,
              expires_at INTEGER NOT NULL, category TEXT NOT NULL,
              status INTEGER NOT NULL, latency_ms INTEGER NOT NULL,
              error_code TEXT NOT NULL, complete INTEGER NOT NULL,
              origin TEXT NOT NULL, input_tokens INTEGER, output_tokens INTEGER,
              capture_truncated INTEGER NOT NULL, content_saved INTEGER NOT NULL DEFAULT 0);
            CREATE INDEX IF NOT EXISTS trace_tenant_time ON traces(key_ref,timestamp,trace_id);
            CREATE INDEX IF NOT EXISTS trace_expiry ON traces(expires_at);
            CREATE TABLE IF NOT EXISTS samples(
              trace_id TEXT PRIMARY KEY REFERENCES traces(trace_id) ON DELETE CASCADE,
              nonce BLOB NOT NULL, ciphertext BLOB NOT NULL,
              consent_at INTEGER NOT NULL, bytes INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS steps(
              trace_id TEXT NOT NULL REFERENCES traces(trace_id) ON DELETE CASCADE,
              sequence INTEGER NOT NULL, kind TEXT NOT NULL,
              skill_ref TEXT NOT NULL, skill_id TEXT NOT NULL, origin TEXT NOT NULL,
              status TEXT NOT NULL, latency_ms INTEGER NOT NULL,
              PRIMARY KEY(trace_id,sequence));
            CREATE TABLE IF NOT EXISTS counters(name TEXT PRIMARY KEY,value INTEGER NOT NULL);
            ''')
        self.path.chmod(0o600)

    @contextmanager
    def connect(self):
        c = sqlite3.connect(self.path, timeout=2, isolation_level='IMMEDIATE')
        c.row_factory = sqlite3.Row
        c.execute('PRAGMA foreign_keys=ON')
        c.execute('PRAGMA busy_timeout=2000')
        c.execute('PRAGMA secure_delete=ON')
        c.execute('PRAGMA wal_autocheckpoint=256')
        c.execute('PRAGMA journal_size_limit=8388608')
        # WAL/SHM inherit the database's private directory; also restrict existing files.
        for p in (self.path, Path(str(self.path)+'-wal'), Path(str(self.path)+'-shm')):
            try:
                p.chmod(0o600)
            except FileNotFoundError:
                pass
        try:
            yield c
            c.commit()
        except BaseException:
            c.rollback()
            raise
        finally:
            c.close()

    def ref(self, domain, value):
        return domain+'_'+hmac.new(self.identity_key, (domain+'\0'+str(value)).encode(), hashlib.sha256).hexdigest()[:32]

    def consent(self, token_id, granted, *, days=None, basis='operator_confirmed'):
        integer(token_id, 1, 2**63-1)
        if type(granted) is not bool or basis != 'operator_confirmed':
            raise ValueError('invalid_consent')
        now = int(self.clock()); key = self.ref('key', token_id)
        with self.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            c.execute('INSERT OR REPLACE INTO consent VALUES(?,?,?,?,?)',
                      (key, int(granted), now, 0 if days is None else now + integer(days, 1, 90)*86400, basis))
            if not granted:
                c.execute('DELETE FROM samples WHERE trace_id IN(SELECT trace_id FROM traces WHERE key_ref=?)', (key,))
                c.execute('UPDATE traces SET content_saved=0 WHERE key_ref=?', (key,))
        return {'key_ref': key, 'granted': granted, 'prior_samples_deleted': not granted}

    def count(self, name):
        if name not in ('ingested', 'duplicate', 'capacity_rejected', 'expired', 'write_failed'):
            raise ValueError('invalid_counter')
        with self.connect() as c:
            c.execute('INSERT INTO counters VALUES(?,1) ON CONFLICT(name) DO UPDATE SET value=value+1', (name,))

    def ingest(self, value):
        if type(value) is not dict or set(value)-{'trace_id','token_id','session_id','category','status','latency_ms','error_code','complete','origin','input_tokens','output_tokens','capture_truncated','question','answer','steps'}:
            raise ValueError('invalid_event')
        token_id = integer(value.get('token_id'), 1, 2**63-1)
        trace = value.get('trace_id')
        if not isinstance(trace, str) or not re.fullmatch(r'[a-f0-9]{32}', trace):
            raise ValueError('invalid_trace_id')
        category = value.get('category', 'other'); error = value.get('error_code', 'none')
        origin = value.get('origin', 'gateway')
        if category not in CATEGORIES or error not in ERRORS or origin != 'gateway':
            raise ValueError('invalid_mechanics')
        for field in ('complete', 'capture_truncated'):
            if type(value.get(field, False)) is not bool:
                raise ValueError('invalid_flag')
        session = value.get('session_id', '')
        if not isinstance(session, str) or len(session) > 256:
            raise ValueError('invalid_session')
        status = integer(value.get('status'), 100, 599)
        latency = integer(value.get('latency_ms'), 0, 86400000)
        usage = [None if value.get(k) is None else integer(value[k], 0, 2**31-1)
                 for k in ('input_tokens', 'output_tokens')]
        steps = value.get('steps', [])
        if type(steps) is not list or len(steps) > 128:
            raise ValueError('invalid_steps')
        normalized = []
        for sequence, step in enumerate(steps):
            if type(step) is not dict or set(step)-{'kind','skill_id','origin','status','latency_ms'}:
                raise ValueError('invalid_step')
            kind = step.get('kind'); source = step.get('origin'); state = step.get('status')
            skill = step.get('skill_id', '')
            if kind not in KINDS or source not in ORIGINS or state not in ('requested','completed','failed','unknown','queued','running','cancelled','requires_approval') or not isinstance(skill, str) or len(skill) > 128:
                raise ValueError('invalid_step')
            if (kind in ('tool.server_executed','search.executed','artifact.created') and source != 'server_executor') or (kind == 'tool.requested' and source != 'model_requested') or (kind == 'tool.client_reported' and source != 'client_reported'):
                raise ValueError('invalid_evidence_origin')
            if kind in ('job.status','artifact.reported') and source!='server_reported':
                raise ValueError('invalid_evidence_origin')
            # Tool names may contain private caller data; persist only their HMAC.
            normalized.append((trace, sequence, kind, self.ref('skill', skill), skill if skill in KNOWN_SKILLS else '', source, state,
                               integer(step.get('latency_ms', 0), 0, 86400000)))
        key = self.ref('key', token_id); now = int(self.clock())
        with self.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            previous=c.execute('SELECT key_ref FROM traces WHERE trace_id=?', (trace,)).fetchone()
            if previous and previous['key_ref']!=key:
                raise ValueError('trace_identity_conflict')
            if previous:
                return {'ok': True, 'duplicate': True, 'trace_id': trace}
            c.execute('DELETE FROM traces WHERE expires_at<=?', (now,))
            consent = c.execute('SELECT * FROM consent WHERE key_ref=? AND granted=1 AND (expires_at=0 OR expires_at>?)', (key, now)).fetchone()
            sample = None
            if consent:
                q = sanitize(value.get('question', ''), 16384)
                a = sanitize(value.get('answer', ''), 65536)
                if q['text'] or a['text']:
                    sample = json.dumps({'question': q, 'answer': a}, ensure_ascii=False).encode()
            pages=c.execute('PRAGMA page_count').fetchone()[0]-c.execute('PRAGMA freelist_count').fetchone()[0]
            occupied=pages*c.execute('PRAGMA page_size').fetchone()[0]
            if c.execute('SELECT COUNT(*) FROM traces').fetchone()[0] >= self.max_events or c.execute('SELECT COUNT(*) FROM traces WHERE key_ref=?',(key,)).fetchone()[0]>=10000 or occupied + len(sample or b'') + 4096 + len(normalized)*512 > self.max_bytes:
                raise ValueError('storage_capacity')
            c.execute('INSERT INTO traces VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                      (trace, key, self.ref('session', str(token_id)+'\0'+session) if session else '',
                       now, now+self.retention, category, status, latency, error,
                       int(value.get('complete', False)), origin, *usage,
                       int(value.get('capture_truncated', False)), int(sample is not None)))
            c.executemany('INSERT INTO steps VALUES(?,?,?,?,?,?,?,?)', normalized)
            if sample:
                nonce = os.urandom(12)
                aad = (key+'\0'+trace).encode()
                encrypted = self.aes.encrypt(nonce, sample, aad)
                c.execute('INSERT INTO samples VALUES(?,?,?,?,?)', (trace, nonce, encrypted, consent['updated_at'], len(encrypted)))
        return {'ok': True, 'trace_id': trace, 'key_ref': key, 'content_saved': sample is not None}

    def list(self, token_id, *, before=None, limit=25, include_content=False):
        integer(token_id, 1, 2**63-1); integer(limit, 1, 100)
        if before is not None and (not isinstance(before, str) or not re.fullmatch(r'[a-f0-9]{32}', before)):
            raise ValueError('invalid_cursor')
        key = self.ref('key', token_id); now = int(self.clock())
        with self.connect() as c:
            rows = c.execute('SELECT * FROM traces WHERE key_ref=? AND expires_at>? AND (? IS NULL OR rowid<(SELECT rowid FROM traces WHERE trace_id=? AND key_ref=?)) ORDER BY rowid DESC LIMIT ?',
                             (key, now, before, before, key, limit)).fetchall()
            result = []
            for row in rows:
                item = dict(row)
                item['steps'] = [dict(s) for s in c.execute('SELECT sequence,kind,skill_ref,skill_id,origin,status,latency_ms FROM steps WHERE trace_id=? ORDER BY sequence', (row['trace_id'],))]
                if include_content:
                    consent = c.execute('SELECT 1 FROM consent WHERE key_ref=? AND granted=1 AND (expires_at=0 OR expires_at>?)', (key, now)).fetchone()
                    sample = c.execute('SELECT * FROM samples WHERE trace_id=?', (row['trace_id'],)).fetchone() if consent else None
                    if sample:
                        item['sample'] = json.loads(self.aes.decrypt(sample['nonce'], sample['ciphertext'], (key+'\0'+row['trace_id']).encode()))
                        item['sample_is_untrusted'] = True
                result.append(item)
        return {'key_ref': key, 'traces': result, 'next_before': result[-1]['trace_id'] if len(result)==limit else None}

    def summary(self, token_id=None):
        now = int(self.clock()); key = self.ref('key', token_id) if token_id is not None else None
        if token_id is not None:integer(token_id,1,2**63-1)
        with self.connect() as c:
            rows = c.execute('SELECT category,error_code,COUNT(*) count,SUM(CASE WHEN status>=400 OR complete=0 OR error_code<>\'none\' THEN 1 ELSE 0 END) failed,MAX(latency_ms) slowest_ms,SUM(content_saved) samples FROM traces WHERE expires_at>? AND (? IS NULL OR key_ref=?) GROUP BY category,error_code', (now,key,key)).fetchall()
            gaps = dict(c.execute('SELECT name,value FROM counters').fetchall())
            jobs=[dict(r) for r in c.execute('SELECT s.status,COUNT(*) count FROM steps s JOIN traces t ON t.trace_id=s.trace_id WHERE s.kind=\'job.status\' AND t.expires_at>? AND (? IS NULL OR t.key_ref=?) GROUP BY s.status',(now,key,key))]
        return {'key_ref':key,'groups':[dict(r) for r in rows],'capture_counters':gaps,
                'reported_job_states':jobs,
                'findings':[{'category':r['category'],'code':'review_failures' if r['failed'] else 'review_latency','count':r['count']}
                            for r in rows if r['failed'] or r['slowest_ms']>60000]+[{'code':'review_reported_job_failure','status':j['status'],'count':j['count']} for j in jobs if j['status'] in ('failed','cancelled')],
                'review_only':True,'automatic_training':False}

    def purge(self, token_id=None):
        now = int(self.clock())
        if token_id is not None:integer(token_id,1,2**63-1)
        with self.connect() as c:
            if token_id is None:
                count=c.execute('DELETE FROM traces WHERE expires_at<=?',(now,)).rowcount
            else:
                count=c.execute('DELETE FROM traces WHERE key_ref=?',(self.ref('key',token_id),)).rowcount
            c.execute('DELETE FROM samples WHERE trace_id IN(SELECT t.trace_id FROM traces t LEFT JOIN consent c ON c.key_ref=t.key_ref WHERE c.key_ref IS NULL OR c.granted=0 OR (c.expires_at<>0 AND c.expires_at<=?))',(now,))
            c.execute('UPDATE traces SET content_saved=0 WHERE NOT EXISTS(SELECT 1 FROM samples s WHERE s.trace_id=traces.trace_id)')
        with self.connect() as c:
            c.execute('PRAGMA wal_checkpoint(PASSIVE)')
        return {'deleted_traces':count}
