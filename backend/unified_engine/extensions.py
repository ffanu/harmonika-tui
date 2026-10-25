"""Durable unified-engine extensions: tenant event bus, verified local corpus, sealed connector secrets.

Real capabilities on the existing encrypted Store only. Event payloads and secret
values are AES-GCM sealed; lookup columns hold purpose-separated HMACs so the
database never stores plaintext topics, payloads, secret names or values. The
verified corpus is a LOCAL operator-supplied snapshot (no network fetch) searched
with BM25 lexical scoring; results cite exact source/sha256/date and are never
certified as independently verified. secret_resolve is an internal function with
an explicit purpose allowlist and must never be routed as a REST endpoint. This
module performs no logging and no network access.
"""
import hashlib
import hmac
import json
import math
import re
import time
import uuid
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

from .state import Error, State, encoded, require

TOPIC_PATTERN = re.compile(r'[A-Za-z0-9_.-]{1,80}')
IDEM_PATTERN = re.compile(r'[A-Za-z0-9_-]{8,128}')  # 8..128 printable ASCII key material.
SECRET_NAME_PATTERN = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,63}')  # Bounded, no path separators.
RESERVED_SECRET_NAMES = frozenset((
    'root', 'provider', 'server', 'env', 'environment', 'gateway', 'system',
    'admin', 'master', 'global', 'operator', 'core', 'internal', 'service',
))

MAX_EVENT_PAYLOAD_BYTES = 16384
MAX_TOPICS_PER_ACTOR = 100
MAX_EVENTS_PER_ACTOR = 1000
MAX_EVENTS_GLOBAL = 10000
EVENT_TTL_SECONDS = 86400

MAX_SECRET_VALUE_BYTES = 4096
MAX_SECRETS_PER_ACTOR = 50
MAX_SECRETS_GLOBAL = 500
MAX_SECRET_ACTOR_CIPHERTEXT = 262144
MAX_SECRET_GLOBAL_CIPHERTEXT = 2 * 1024 * 1024
SECRET_PURPOSE_ALLOWLIST = frozenset(('internal_connector',))

CORPUS_MAX_DOCS = 2000
CORPUS_MAX_DOC_CHARS = 262144
CORPUS_MAX_TOTAL_CHARS = 4 * 1024 * 1024
CORPUS_MAX_FILE_BYTES = 8 * 1024 * 1024
CORPUS_QUERY_MAX_CHARS = 400
CORPUS_SNIPPET_CHARS = 200
_DOCUMENT_FIELDS = frozenset(('id', 'title', 'text', 'source', 'sha256', 'verified_at'))
_DOCUMENT_ID = re.compile(r'[A-Za-z0-9_.-]{1,128}')
_HTTP_SOURCE = re.compile(r'https?://[^\s]{1,498}')
_ISO_DATE = re.compile(r'\d{4}-\d{2}-\d{2}')
_TOKEN = re.compile(r'[a-z0-9]+')

_SCHEMA = '''
CREATE TABLE IF NOT EXISTS unified_ext_events(
 id TEXT PRIMARY KEY,actor TEXT NOT NULL,topic TEXT NOT NULL,seq INTEGER NOT NULL,
 idem TEXT NOT NULL,digest TEXT NOT NULL,sealed BLOB NOT NULL,
 created REAL NOT NULL,expires REAL NOT NULL,UNIQUE(actor,topic,seq),UNIQUE(actor,topic,idem));
CREATE INDEX IF NOT EXISTS unified_ext_events_expiry ON unified_ext_events(expires);
CREATE TABLE IF NOT EXISTS unified_ext_topics(actor TEXT NOT NULL,topic TEXT NOT NULL,last_seq INTEGER NOT NULL,PRIMARY KEY(actor,topic));
INSERT OR IGNORE INTO unified_ext_topics SELECT actor,topic,MAX(seq) FROM unified_ext_events GROUP BY actor,topic;
CREATE TABLE IF NOT EXISTS unified_ext_secrets(
 actor TEXT NOT NULL,name TEXT NOT NULL,idem TEXT NOT NULL,digest TEXT NOT NULL,
 sealed BLOB NOT NULL,created REAL NOT NULL,updated REAL NOT NULL,PRIMARY KEY(actor,name));
CREATE TABLE IF NOT EXISTS unified_ext_secret_ops(actor TEXT NOT NULL,token TEXT NOT NULL,digest TEXT NOT NULL,sealed BLOB NOT NULL,expires REAL NOT NULL,PRIMARY KEY(actor,token));
'''


def _tokens(text):
    return _TOKEN.findall(text.lower())


class _Corpus:
    """In-memory BM25 index over an already-validated local snapshot."""

    def __init__(self, documents):
        self.documents = documents
        self.lengths = []
        postings = {}
        for index, document in enumerate(documents):
            counts = {}
            for token in _tokens(document['title']) + _tokens(document['text']):
                counts[token] = counts.get(token, 0) + 1
            for token, count in counts.items():
                postings.setdefault(token, []).append((index, count))
            self.lengths.append(sum(counts.values()))
        self.postings = postings
        self.average = (sum(self.lengths) / len(self.lengths)) or 1.0

    def search(self, query, limit):
        scores = {}
        for term in dict.fromkeys(_tokens(query)):
            matches = self.postings.get(term)
            if not matches:
                continue
            idf = math.log(1 + (len(self.documents) - len(matches) + 0.5) / (len(matches) + 0.5))
            for index, count in matches:
                denominator = count + 1.5 * (1 - 0.75 + 0.75 * self.lengths[index] / self.average)
                scores[index] = scores.get(index, 0.0) + idf * count * 2.5 / denominator
        ranked = sorted(scores.items(), key=lambda item: (-item[1], self.documents[item[0]]['id']))[:limit]
        return [self._result(index, score) for index, score in ranked]

    def _result(self, index, score):
        document = self.documents[index]
        text = document['text']
        return {
            'id': document['id'],
            'title': document['title'],
            'score': round(score, 4),
            'excerpt': text[:CORPUS_SNIPPET_CHARS] + ('...' if len(text) > CORPUS_SNIPPET_CHARS else ''),
            'citation': {
                'source': document['source'],
                'sha256': document['sha256'],
                'verified_at': document['verified_at'],
            },
            'independently_verified': False,
        }


class Extensions:
    """Extensions bound to one encrypted State; owns its tables without touching state.py."""

    def __init__(self, state):
        require(isinstance(state, State), 'invalid_state', 500)
        self.state = state
        self._corpus = None
        with state.store._transaction() as db:
            db.executescript(_SCHEMA)

    # ------------------------------------------------------------------ events

    def publish(self, actor, topic, payload, idem):
        """Seal one tenant-scoped event; sequence is monotonic per actor+topic."""
        owner = self.state.owner(actor)
        require(type(topic) is str and TOPIC_PATTERN.fullmatch(topic) is not None, 'invalid_topic')
        require(type(idem) is str and IDEM_PATTERN.fullmatch(idem) is not None, 'idempotency_required')
        require(type(payload) is dict, 'invalid_payload')
        raw = encoded(payload)
        require(len(raw) <= MAX_EVENT_PAYLOAD_BYTES, 'event_payload_limit', 413)
        digest = self.state.store._hash('unified_event_payload', raw.decode())
        topic_hash = self.state.store._hash('unified_event_topic', owner, topic)
        idem_hash = self.state.store._hash('unified_event_idem', owner, topic_hash, idem)
        now = time.time()
        with self.state.store._transaction() as db:
            db.execute('DELETE FROM unified_ext_events WHERE expires<?', (now,))
            row = db.execute('SELECT * FROM unified_ext_events WHERE actor=? AND topic=? AND idem=?',
                             (owner, topic_hash, idem_hash)).fetchone()
            if row is not None:
                require(hmac.compare_digest(row['digest'], digest), 'idempotency_conflict', 409)
                sealed = self.state.store._unseal(row['sealed'], 'unified', row['actor'], 'event', row['id'])
                return {**_public_event(sealed), 'replayed': True}
            if db.execute('SELECT 1 FROM unified_ext_topics WHERE actor=? AND topic=? LIMIT 1',
                          (owner, topic_hash)).fetchone() is None:
                require(db.execute('SELECT COUNT(*) FROM unified_ext_topics WHERE actor=?',
                                   (owner,)).fetchone()[0] < MAX_TOPICS_PER_ACTOR, 'topic_quota', 429)
                require(db.execute('SELECT COUNT(*) FROM unified_ext_topics').fetchone()[0]<10000,'topic_capacity',429)
                db.execute('INSERT INTO unified_ext_topics VALUES(?,?,0)',(owner,topic_hash))
            require(db.execute('SELECT COUNT(*) FROM unified_ext_events WHERE actor=?',
                               (owner,)).fetchone()[0] < MAX_EVENTS_PER_ACTOR, 'event_quota', 429)
            require(db.execute('SELECT COUNT(*) FROM unified_ext_events').fetchone()[0]
                    < MAX_EVENTS_GLOBAL, 'event_capacity', 429)
            sequence = db.execute('SELECT last_seq+1 FROM unified_ext_topics WHERE actor=? AND topic=?',
                                  (owner, topic_hash)).fetchone()[0]
            db.execute('UPDATE unified_ext_topics SET last_seq=? WHERE actor=? AND topic=?',(sequence,owner,topic_hash))
            event_id = str(uuid.uuid4())
            value = {'event_id': event_id, 'sequence': sequence, 'topic': topic,
                     'payload': payload, 'timestamp': now}
            sealed = self.state.store._seal(value, 'unified', owner, 'event', event_id)
            self.state.admission_budget(db,len(sealed))
            db.execute('INSERT INTO unified_ext_events VALUES(?,?,?,?,?,?,?,?,?)',
                       (event_id, owner, topic_hash, sequence, idem_hash, digest, sealed,
                        now, now + EVENT_TTL_SECONDS))
            return {**_public_event(value), 'replayed': False}

    def replay(self, actor, topic, after_sequence=0, limit=100):
        """Return this actor's sealed events on one topic after a sequence cursor."""
        owner = self.state.owner(actor)
        require(type(topic) is str and TOPIC_PATTERN.fullmatch(topic) is not None, 'invalid_topic')
        require(type(after_sequence) is int and 0 <= after_sequence <= 2 ** 31, 'invalid_after_sequence')
        require(type(limit) is int and 1 <= limit <= 100, 'invalid_limit')
        topic_hash = self.state.store._hash('unified_event_topic', owner, topic)
        with self.state.store._transaction() as db:
            db.execute('DELETE FROM unified_ext_events WHERE expires<?', (time.time(),))
            rows = db.execute('SELECT * FROM unified_ext_events WHERE actor=? AND topic=? AND seq>? '
                              'ORDER BY seq LIMIT ?', (owner, topic_hash, after_sequence, limit)).fetchall()
        return [_public_event(self.state.store._unseal(row['sealed'], 'unified', row['actor'], 'event', row['id']))
                for row in rows]

    # ----------------------------------------------------------------- corpus

    def load_corpus(self, path):
        """Validate and load a LOCAL operator snapshot; fail closed on any mismatch."""
        file = Path(path)
        if not file.is_file() or file.is_symlink():
            raise Error('corpus_not_found', 404)
        require(file.stat().st_size <= CORPUS_MAX_FILE_BYTES, 'corpus_limit', 413)
        try:
            def unique(pairs):
                value={}
                for k,v in pairs:
                    require(k not in value,'corpus_duplicate_field');value[k]=v
                return value
            raw = json.loads(file.read_text(encoding='utf-8'),object_pairs_hook=unique)
        except (OSError, ValueError, UnicodeError, RecursionError):
            raise Error('corpus_invalid', 400) from None
        require(type(raw) is dict and set(raw) == {'documents'}, 'corpus_invalid')
        entries = raw['documents']
        require(type(entries) is list and 0 < len(entries) <= CORPUS_MAX_DOCS, 'corpus_invalid')
        documents, seen, total_chars = [], set(), 0
        for entry in entries:
            require(type(entry) is dict and set(entry) == _DOCUMENT_FIELDS, 'corpus_invalid')
            identifier, title, text = entry['id'], entry['title'], entry['text']
            require(type(identifier) is str and _DOCUMENT_ID.fullmatch(identifier) is not None,
                    'invalid_corpus_document_id')
            require(identifier not in seen, 'corpus_duplicate_id')
            require(type(title) is str and 0 < len(title) <= 256
                    and not any(ord(c) < 32 or ord(c) == 127 for c in title), 'invalid_corpus_title')
            require(type(text) is str and 0 < len(text) <= CORPUS_MAX_DOC_CHARS, 'corpus_limit', 413)
            claimed = entry['sha256']
            require(type(claimed) is str and re.fullmatch(r'[0-9a-fA-F]{64}', claimed) is not None,
                    'invalid_corpus_hash')
            # Exact content hash verification; any mismatch fails the whole load.
            require(hmac.compare_digest(hashlib.sha256(text.encode('utf-8')).hexdigest(), claimed.lower()),
                    'corpus_hash_mismatch')
            source = entry['source']
            require(type(source) is str and 0 < len(source.strip()) <= 512
                    and not any(ord(c) < 32 for c in source), 'invalid_corpus_source')
            try:
                parsed=urlsplit(source);port=parsed.port
                require(parsed.scheme in ('http','https') and parsed.hostname and not parsed.username and not parsed.password and _HTTP_SOURCE.fullmatch(source) is not None,'untrusted_corpus_source')
            except ValueError:raise Error('untrusted_corpus_source') from None
            verified_at = entry['verified_at']
            require(type(verified_at) is str and _ISO_DATE.fullmatch(verified_at) is not None,
                    'invalid_corpus_date')
            try:
                datetime.strptime(verified_at, '%Y-%m-%d')
            except ValueError:
                raise Error('invalid_corpus_date') from None
            seen.add(identifier)
            total_chars += len(text)
            require(total_chars <= CORPUS_MAX_TOTAL_CHARS, 'corpus_limit', 413)
            documents.append(dict(entry))
        self._corpus = _Corpus(documents)  # Atomic swap: only fully validated snapshots load.
        return {'documents': len(documents), 'method': 'local_lexical_bm25', 'vector_db': False}

    def query(self, query, limit=5):
        """BM25 lexical search over the verified local corpus; honest about method and certainty."""
        require(type(query) is str and 0 < len(query.strip()) <= CORPUS_QUERY_MAX_CHARS, 'invalid_query')
        require(type(limit) is int and 1 <= limit <= 20, 'invalid_limit')
        corpus = self._corpus
        if corpus is None:
            raise Error('corpus_unavailable', 503)
        return {
            'method': 'local_lexical_bm25',
            'vector_db': False,
            'independently_verified': False,
            'corpus_documents': len(corpus.documents),
            'results': corpus.search(query, limit),
        }

    # ---------------------------------------------------------------- secrets

    def _secret_name(self, name):
        require(type(name) is str and SECRET_NAME_PATTERN.fullmatch(name) is not None, 'invalid_secret_name')
        require(name.lower() not in RESERVED_SECRET_NAMES, 'secret_name_reserved', 403)

    def _secret_op(self,db,owner,name_hash,idem,operation,value=''):
        store=self.state.store;token=store._hash('unified_secret_op',owner,name_hash,idem)
        digest=store._hash('unified_secret_op_request',owner,name_hash,operation,value)
        db.execute('DELETE FROM unified_ext_secret_ops WHERE expires<?',(time.time(),))
        row=db.execute('SELECT * FROM unified_ext_secret_ops WHERE actor=? AND token=?',(owner,token)).fetchone()
        result=None
        if row is not None:
            require(hmac.compare_digest(row['digest'],digest),'idempotency_conflict',409)
            result=store._unseal(row['sealed'],'unified',owner,'secret_op',token);result['replayed']=True
        return token,digest,result

    def _save_secret_op(self,db,owner,token,digest,result):
        require(db.execute('SELECT COUNT(*) FROM unified_ext_secret_ops WHERE actor=?',(owner,)).fetchone()[0]<1000,'secret_idempotency_quota',429)
        require(db.execute('SELECT COUNT(*) FROM unified_ext_secret_ops').fetchone()[0]<10000,'secret_idempotency_capacity',429)
        sealed=self.state.store._seal(result,'unified',owner,'secret_op',token)
        if not result.get('deleted'):self.state.admission_budget(db,len(sealed))
        db.execute('INSERT INTO unified_ext_secret_ops VALUES(?,?,?,?,?)',(owner,token,digest,sealed,time.time()+86400))
        return result

    def secret_set(self, actor, name, value, idem):
        """Seal a tenant connector credential; idempotent per name+key, conflict on changed value."""
        owner = self.state.owner(actor)
        self._secret_name(name)
        require(type(idem) is str and IDEM_PATTERN.fullmatch(idem) is not None, 'idempotency_required')
        require(type(value) is str and value != '', 'invalid_secret_value')
        require(len(value.encode('utf-8')) <= MAX_SECRET_VALUE_BYTES, 'secret_value_limit', 413)
        store = self.state.store
        name_hash = store._hash('unified_secret_name', owner, name)
        idem_hash = store._hash('unified_secret_idem', owner, name_hash, idem)
        digest = store._hash('unified_secret_request', owner, value)
        now = time.time()
        with store._transaction() as db:
            token,op_digest,replay=self._secret_op(db,owner,name_hash,idem,'set',value)
            if replay is not None:return replay
            row = db.execute('SELECT * FROM unified_ext_secrets WHERE actor=? AND name=?',
                             (owner, name_hash)).fetchone()
            if row is not None and row['idem'] == idem_hash:
                require(hmac.compare_digest(row['digest'], digest), 'idempotency_conflict', 409)
                return self._save_secret_op(db,owner,token,op_digest,{'name': name, 'created_at': row['created'], 'updated_at': row['updated'], 'replayed': True})
            sealed = store._seal({'name': name, 'value': value}, 'unified', owner, 'secret', name_hash)
            self.state.admission_budget(db,max(0,len(sealed)-(len(row['sealed']) if row else 0))+512)
            if row is None:
                require(db.execute('SELECT COUNT(*) FROM unified_ext_secrets WHERE actor=?',
                                   (owner,)).fetchone()[0] < MAX_SECRETS_PER_ACTOR, 'secret_quota', 429)
                require(db.execute('SELECT COUNT(*) FROM unified_ext_secrets').fetchone()[0]
                        < MAX_SECRETS_GLOBAL, 'secret_capacity', 429)
                actor_bytes = db.execute('SELECT COALESCE(SUM(length(sealed)),0) FROM unified_ext_secrets '
                                         'WHERE actor=?', (owner,)).fetchone()[0] + len(sealed)
                require(actor_bytes <= MAX_SECRET_ACTOR_CIPHERTEXT, 'secret_storage_capacity', 429)
                global_bytes = db.execute('SELECT COALESCE(SUM(length(sealed)),0) FROM '
                                          'unified_ext_secrets').fetchone()[0] + len(sealed)
                require(global_bytes <= MAX_SECRET_GLOBAL_CIPHERTEXT, 'secret_storage_capacity', 429)
                db.execute('INSERT INTO unified_ext_secrets VALUES(?,?,?,?,?,?,?)',
                           (owner, name_hash, idem_hash, digest, sealed, now, now))
                return self._save_secret_op(db,owner,token,op_digest,{'name': name, 'created_at': now, 'updated_at': now, 'replayed': False})
            actor_bytes = db.execute('SELECT COALESCE(SUM(length(sealed)),0) FROM unified_ext_secrets '
                                     'WHERE actor=?', (owner,)).fetchone()[0] + len(sealed) - len(row['sealed'])
            require(actor_bytes <= MAX_SECRET_ACTOR_CIPHERTEXT, 'secret_storage_capacity', 429)
            global_bytes=db.execute('SELECT COALESCE(SUM(length(sealed)),0) FROM unified_ext_secrets').fetchone()[0]+len(sealed)-len(row['sealed'])
            require(global_bytes<=MAX_SECRET_GLOBAL_CIPHERTEXT,'secret_storage_capacity',429)
            db.execute('UPDATE unified_ext_secrets SET idem=?,digest=?,sealed=?,updated=? WHERE actor=? AND name=?',
                       (idem_hash, digest, sealed, now, owner, name_hash))
            return self._save_secret_op(db,owner,token,op_digest,{'name': name, 'created_at': row['created'], 'updated_at': now, 'replayed': False})

    def secret_metadata(self, actor, name):
        """Sanitized metadata only: never the value, ciphertext or any suffix of it."""
        owner = self.state.owner(actor)
        self._secret_name(name)
        name_hash = self.state.store._hash('unified_secret_name', owner, name)
        with self.state.store._transaction() as db:
            row = db.execute('SELECT created,updated FROM unified_ext_secrets WHERE actor=? AND name=?',
                             (owner, name_hash)).fetchone()
            require(row is not None, 'not_found', 404)
            return {'name': name, 'created_at': row['created'], 'updated_at': row['updated']}

    def secret_delete(self, actor, name, idem):
        owner = self.state.owner(actor)
        self._secret_name(name)
        require(type(idem) is str and IDEM_PATTERN.fullmatch(idem) is not None, 'idempotency_required')
        name_hash = self.state.store._hash('unified_secret_name', owner, name)
        with self.state.store._transaction() as db:
            token,digest,replay=self._secret_op(db,owner,name_hash,idem,'delete')
            if replay is not None:return replay
            row = db.execute('SELECT 1 FROM unified_ext_secrets WHERE actor=? AND name=?',
                             (owner, name_hash)).fetchone()
            require(row is not None, 'not_found', 404)
            db.execute('DELETE FROM unified_ext_secrets WHERE actor=? AND name=?', (owner, name_hash))
            return self._save_secret_op(db,owner,token,digest,{'name': name, 'deleted': True,'replayed':False})

    def secret_resolve(self, actor, name, purpose):
        """INTERNAL ONLY, never a REST endpoint: explicit purpose allowlist gates plaintext release."""
        owner = self.state.owner(actor)
        self._secret_name(name)
        require(purpose in SECRET_PURPOSE_ALLOWLIST, 'purpose_not_allowed', 403)
        name_hash = self.state.store._hash('unified_secret_name', owner, name)
        with self.state.store._transaction() as db:
            row = db.execute('SELECT * FROM unified_ext_secrets WHERE actor=? AND name=?',
                             (owner, name_hash)).fetchone()
            require(row is not None, 'not_found', 404)
            return self.state.store._unseal(row['sealed'], 'unified', owner, 'secret', name_hash)['value']


def _public_event(value):
    return {'event_id': value['event_id'], 'sequence': value['sequence'], 'topic': value['topic'],
            'payload': value['payload'], 'timestamp': value['timestamp']}
