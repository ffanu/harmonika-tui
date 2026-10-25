"""Private encrypted memory and synchronous, explicitly allowlisted workflow state.

The actor must come from the authenticated gateway, never an input field. All
customer strings, definitions and cached results are sealed; lookup columns use
purpose-separated HMACs. This module contains no scheduler or network runner.
"""
import copy
import hashlib
import hmac
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import stat
import time
import uuid

MAX_VALUE_BYTES = 16384
MAX_INPUT_BYTES = 65536
MAX_OUTPUT_BYTES = 131072
MAX_RECORDS = 100  # Memory records plus workflow definitions per actor.
MAX_ACTOR_BYTES = 1048576  # Includes encrypted replay cache and run reservations.
MAX_ACTORS = 1000
MAX_GLOBAL_BYTES = 100 * 1048576
MAX_IDEMPOTENCY_ACTOR = 1000
MAX_IDEMPOTENCY_GLOBAL = 50000
IDEMPOTENCY_TTL = 86400
RUN_LEASE_SECONDS = 60  # Expiration requires recovery, never automatic reexecution.
RUN_BUDGET_SECONDS = 15

PURE_SKILLS = frozenset((
    'data.read_csv', 'data.read_json', 'data.transform', 'data.analyze',
    'data.visualize', 'data.export', 'ip.lookup', 'bot.useragent.analyze',
    'payment.velocity.check', 'behavior.velocity', 'nlu.spam.detect',
    'security.pii.mask',
))
_ACTOR = re.compile(r'api-token:[1-9][0-9]{0,18}')
_IDEMPOTENCY = re.compile(r'[A-Za-z0-9_-]{8,128}')
_SCOPE = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}')
_STEP = re.compile(r'[A-Za-z][A-Za-z0-9_-]{0,39}')
_UUID = re.compile(r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}')
_UNSAFE_FIELDS = frozenset((
    'path', 'file_path', 'filename', 'command', 'shell', 'url', 'endpoint',
    'actor', 'principal', 'authorization', 'credential', 'credentials',
    'secret', 'idempotency_key', '$ref', '$step', 'template',
))


class StateError(ValueError):
    def __init__(self, code, status=400):
        self.code, self.status = code, status
        super().__init__(code)


def _json_bytes(value, maximum=MAX_INPUT_BYTES, *, max_depth=20, max_nodes=20000):
    pending = [(value, 0)]
    visited = 0
    while pending:
        item, depth = pending.pop()
        visited += 1
        if visited > max_nodes or depth > max_depth:
            raise StateError('json_complexity')
        if type(item) is dict:
            if len(item) + visited + len(pending) > max_nodes or any(
                    type(k) is not str or len(k) > 128 for k in item):
                raise StateError('invalid_json_fields')
            pending.extend((v, depth + 1) for v in item.values())
        elif type(item) is list:
            if len(item) + visited + len(pending) > max_nodes:
                raise StateError('json_complexity')
            pending.extend((v, depth + 1) for v in item)
        elif type(item) not in (str, int, float, bool, type(None)):
            raise StateError('invalid_json')
        elif type(item) is int and item.bit_length()>4096:
            raise StateError('numeric_precision')
        elif type(item) is float and not math.isfinite(item):
            raise StateError('nonfinite_json')
        elif type(item) is str and len(item) > maximum:
            raise StateError('json_size_limit', 413)
    try:
        encoded = json.dumps(value, sort_keys=True, ensure_ascii=False,
                             separators=(',', ':'), allow_nan=False).encode('utf-8')
    except (ValueError, UnicodeError, TypeError, RecursionError):
        raise StateError('invalid_json') from None
    if len(encoded) > maximum:
        raise StateError('json_size_limit', 413)
    return encoded


def _text(value, field, maximum):
    if type(value) is not str or not value.strip() or len(value) > maximum or any(
            ord(c) < 32 or ord(c) == 127 for c in value):
        raise StateError('invalid_' + field)
    return value


def _object(value, allowed, required):
    if type(value) is not dict or set(value) - set(allowed) or not set(required) <= set(value):
        raise StateError('invalid_input_fields')


def _integer(value, name, low, high):
    if type(value) is not int or not low <= value <= high:
        raise StateError('invalid_' + name)
    return value


def _scope(value):
    if type(value) is not str or not _SCOPE.fullmatch(value):
        raise StateError('invalid_scope')
    return value


def _workflow(value):
    _object(value, ('name', 'steps'), ('name', 'steps'))
    _text(value['name'], 'name', 64)
    steps = value['steps']
    if type(steps) is not list or not 1 <= len(steps) <= 12:
        raise StateError('invalid_workflow_steps')
    ids, normalized = set(), []
    for step in steps:
        _object(step, ('id', 'skill_id', 'input', 'depends_on'), ('id', 'skill_id', 'input'))
        sid = step['id']
        if type(sid) is not str or not _STEP.fullmatch(sid) or sid in ids:
            raise StateError('invalid_workflow_step_id')
        if type(step['skill_id']) is not str or step['skill_id'] not in PURE_SKILLS:
            raise StateError('workflow_skill_not_allowed', 403)
        if type(step['input']) is not dict:
            raise StateError('invalid_step_input')
        _json_bytes(step['input'], MAX_VALUE_BYTES, max_depth=8, max_nodes=5000)
        allowed_fields={
            'data.read_csv':{'text','delimiter'},'data.read_json':{'text'},
            'data.transform':{'rows','operations'},'data.analyze':{'rows'},
            'data.visualize':{'rows','chart_type','x','y'},'data.export':{'rows','format'},
            'ip.lookup':{'ip'},'bot.useragent.analyze':{'user_agent'},
            'payment.velocity.check':{'events','window_seconds','threshold'},
            'behavior.velocity':{'events','window_seconds','threshold'},
            'nlu.spam.detect':{'text'},'security.pii.mask':{'text'},
        }[step['skill_id']]
        if set(step['input'])-allowed_fields:raise StateError('unsafe_workflow_input',403)
        pending = [step['input']]
        while pending:
            item = pending.pop()
            if type(item) is dict:
                pending.extend(item.values())
            elif type(item) is list:
                pending.extend(item)
            elif type(item) is str and re.search(r'\$\{|\{\{|\$steps\b', item):
                raise StateError('workflow_interpolation_not_supported')
        deps = step.get('depends_on', [])
        if type(deps) is not list or len(deps) > 12 or any(
                type(d) is not str or not _STEP.fullmatch(d) for d in deps) or len(set(deps)) != len(deps):
            raise StateError('invalid_workflow_dependencies')
        normalized.append({'id': sid, 'skill_id': step['skill_id'],
                           'input': step['input'], 'depends_on': deps})
        ids.add(sid)
    if any(d not in ids for step in normalized for d in step['depends_on']):
        raise StateError('unknown_workflow_dependency')
    remaining, ordered, done = normalized[:], [], set()
    while remaining:
        step = next((s for s in remaining if set(s['depends_on']) <= done), None)
        if step is None:
            raise StateError('workflow_cycle')
        remaining.remove(step)
        ordered.append(step)
        done.add(step['id'])
    return {'name': value['name'], 'steps': ordered}


def _validate(skill_id, value):
    _json_bytes(value, max_depth=8, max_nodes=5000)
    if skill_id == 'memory.set':
        _object(value, ('scope', 'key', 'value', 'ttl_seconds'), ('key', 'value'))
        _scope(value.get('scope', 'default'))
        _text(value['key'], 'key', 128)
        _json_bytes(value['value'], MAX_VALUE_BYTES)
        _integer(value.get('ttl_seconds', 86400), 'ttl_seconds', 60, 604800)
    elif skill_id == 'memory.get':
        _object(value, ('scope', 'key'), ('key',))
        _scope(value.get('scope', 'default'))
        _text(value['key'], 'key', 128)
    elif skill_id == 'memory.search':
        _object(value, ('scope', 'query', 'limit', 'offset'), ('query',))
        _scope(value.get('scope', 'default'))
        _text(value['query'], 'query', 100)
        _integer(value.get('limit', 10), 'limit', 1, 20)
        _integer(value.get('offset', 0), 'offset', 0, 100)
    elif skill_id == 'memory.clear':
        _object(value, ('scope', 'all'), ('scope', 'all'))
        _scope(value['scope'])
        if value['all'] is not True:
            raise StateError('explicit_clear_confirmation_required')
    elif skill_id == 'workflow.define':
        return _workflow(value)
    elif skill_id == 'workflow.run':
        _object(value, ('workflow_id',), ('workflow_id',))
        if type(value['workflow_id']) is not str or not _UUID.fullmatch(value['workflow_id']):
            raise StateError('invalid_workflow_id')
    else:
        raise StateError('unknown_skill', 404)
    return value


def _schema(properties, required):
    return {'type': 'object', 'properties': properties,
            'required': list(required), 'additionalProperties': False}


def descriptors():
    scope = {'type': 'string', 'pattern': '^' + _SCOPE.pattern + '$', 'maxLength': 64, 'default': 'default'}
    key = {'type': 'string', 'minLength': 1, 'maxLength': 128}
    step = _schema({
        'id': {'type': 'string', 'pattern': '^' + _STEP.pattern + '$', 'maxLength': 40},
        'skill_id': {'type': 'string', 'enum': sorted(PURE_SKILLS)},
        'input': {'type': 'object'},
        'depends_on': {'type': 'array', 'maxItems': 12, 'uniqueItems': True,
                       'items': {'type': 'string', 'pattern': '^' + _STEP.pattern + '$', 'maxLength': 40}},
    }, ('id', 'skill_id', 'input'))
    schemas = {
        'memory.set': _schema({'scope': scope, 'key': key, 'value': {},
            'ttl_seconds': {'type': 'integer', 'minimum': 60, 'maximum': 604800, 'default': 86400}}, ('key', 'value')),
        'memory.get': _schema({'scope': scope, 'key': key}, ('key',)),
        'memory.search': _schema({'scope': scope,
            'query': {'type': 'string', 'minLength': 1, 'maxLength': 100},
            'limit': {'type': 'integer', 'minimum': 1, 'maximum': 20, 'default': 10},
            'offset': {'type': 'integer', 'minimum': 0, 'maximum': 100, 'default': 0}}, ('query',)),
        'memory.clear': _schema({'scope': scope, 'all': {'const': True}}, ('scope', 'all')),
        'workflow.define': _schema({'name': {'type': 'string', 'minLength': 1, 'maxLength': 64},
            'steps': {'type': 'array', 'minItems': 1, 'maxItems': 12, 'items': step}}, ('name', 'steps')),
        'workflow.run': _schema({'workflow_id': {'type': 'string', 'pattern': '^' + _UUID.pattern + '$'}}, ('workflow_id',)),
    }
    descriptions = {
        'memory.set': 'Seal actor-scoped JSON memory with a bounded TTL; idempotency required.',
        'memory.get': 'Read unexpired memory owned by the authenticated actor and scope.',
        'memory.search': 'Bounded literal case-insensitive search over owned keys and JSON values.',
        'memory.clear': 'Clear one explicit actor-owned scope with all=true; idempotency required.',
        'workflow.define': 'Seal a DAG of allowlisted pure input tools; no interpolation or schedules.',
        'workflow.run': 'Run owned DAG sequentially with durable idempotency; uncertain runs require recovery.',
    }
    return {sid: {'description': descriptions[sid], 'input_schema': copy.deepcopy(schema)}
            for sid, schema in schemas.items()}


def _result(skill_id, data):
    return {'status': 'completed', 'skill_id': skill_id, 'data': data, 'replayed': False}


class Store:
    def __init__(self, db_path, secret: bytes):
        if type(secret) is not bytes or len(secret) < 32:
            raise StateError('state_secret_required')
        try:
            from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        except ImportError:
            raise StateError('state_crypto_unavailable', 503) from None
        self._aes = AESGCM(hashlib.sha256(secret + b'\0core-state-encryption-v1').digest())
        self._hmac = hashlib.sha256(secret + b'\0core-state-lookup-v1').digest()
        self.path = Path(db_path).absolute()
        self._closed = False
        parent = self.path.parent
        try:
            parent.mkdir(mode=0o700, exist_ok=True)
            p = parent.lstat()
            if not stat.S_ISDIR(p.st_mode) or p.st_uid != os.getuid() or stat.S_IMODE(p.st_mode) != 0o700:
                raise StateError('unsafe_state_directory', 503)
            flags = os.O_RDWR | os.O_CREAT | getattr(os, 'O_NOFOLLOW', 0)
            fd = os.open(str(self.path), flags, 0o600)
            try:
                s = os.fstat(fd)
                if not stat.S_ISREG(s.st_mode) or s.st_uid != os.getuid() or stat.S_IMODE(s.st_mode) != 0o600 or s.st_nlink != 1:
                    raise StateError('unsafe_state_database', 503)
                self._identity = (s.st_dev, s.st_ino)
            finally:
                os.close(fd)
        except OSError:
            raise StateError('unsafe_state_path', 503) from None
        with self._transaction() as db:
            schema = '''
                CREATE TABLE IF NOT EXISTS metadata (id INTEGER PRIMARY KEY, sealed BLOB NOT NULL);
                CREATE TABLE IF NOT EXISTS actors (actor TEXT PRIMARY KEY);
                CREATE TABLE IF NOT EXISTS memory (
                    actor TEXT NOT NULL, scope TEXT NOT NULL, key TEXT NOT NULL,
                    sealed BLOB NOT NULL, expires INTEGER NOT NULL,
                    PRIMARY KEY(actor,scope,key));
                CREATE TABLE IF NOT EXISTS workflows (
                    actor TEXT NOT NULL, id TEXT NOT NULL, sealed BLOB NOT NULL,
                    PRIMARY KEY(actor,id));
                CREATE TABLE IF NOT EXISTS idempotency (
                    actor TEXT NOT NULL, token TEXT NOT NULL, digest TEXT NOT NULL,
                    state TEXT NOT NULL, result BLOB, run_id TEXT,
                    created INTEGER NOT NULL, expires INTEGER NOT NULL,
                    reserved INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY(actor,token));
                CREATE INDEX IF NOT EXISTS memory_expiry ON memory(expires);
                CREATE INDEX IF NOT EXISTS replay_expiry ON idempotency(expires);
            '''
            for statement in schema.split(';'):
                if statement.strip():
                    db.execute(statement)
            row = db.execute('SELECT sealed FROM metadata WHERE id=1').fetchone()
            if row is None:
                db.execute('INSERT INTO metadata VALUES(1,?)',
                           (self._seal({'format': 'core-state-v1'}, 'metadata'),))
            elif self._unseal(row['sealed'], 'metadata') != {'format': 'core-state-v1'}:
                raise StateError('state_key_mismatch', 503)

    def _hash(self, purpose, *values):
        message = json.dumps([purpose, *values], ensure_ascii=False, separators=(',', ':')).encode()
        return hmac.new(self._hmac, message, hashlib.sha256).hexdigest()

    def _aad(self, purpose, *values):
        return json.dumps(['core-state-v1', purpose, *values], separators=(',', ':')).encode()

    def _seal(self, value, purpose, *values):
        nonce = os.urandom(12)
        return nonce + self._aes.encrypt(nonce, _json_bytes(value, MAX_OUTPUT_BYTES), self._aad(purpose, *values))

    def _unseal(self, value, purpose, *values):
        try:
            return json.loads(self._aes.decrypt(value[:12], value[12:], self._aad(purpose, *values)))
        except Exception:
            raise StateError('state_integrity_failure', 503) from None

    def _connect(self):
        if self._closed:
            raise StateError('state_closed', 503)
        db = None
        try:
            parent = self.path.parent.lstat()
            if not stat.S_ISDIR(parent.st_mode) or parent.st_uid != os.getuid() or stat.S_IMODE(parent.st_mode) != 0o700:
                raise StateError('unsafe_state_directory', 503)
            s = self.path.lstat()
            if not stat.S_ISREG(s.st_mode) or s.st_nlink != 1 or s.st_uid != os.getuid() or stat.S_IMODE(s.st_mode) != 0o600 or (s.st_dev, s.st_ino) != self._identity:
                raise StateError('unsafe_state_database', 503)
            db = sqlite3.connect(str(self.path), timeout=2, isolation_level=None)
            db.row_factory = sqlite3.Row
            db.execute('PRAGMA busy_timeout=2000')
            db.execute('PRAGMA journal_mode=DELETE')
            db.execute('PRAGMA synchronous=FULL')
            db.execute('PRAGMA secure_delete=ON')
            db.execute('PRAGMA temp_store=MEMORY')
            db.execute('PRAGMA trusted_schema=OFF')
            page_size = db.execute('PRAGMA page_size').fetchone()[0]
            db.execute('PRAGMA max_page_count=' + str(MAX_GLOBAL_BYTES // page_size))
            return db
        except sqlite3.Error:
            if db is not None:
                db.close()
            raise StateError('state_unavailable', 503) from None
        except OSError:
            raise StateError('unsafe_state_path', 503) from None

    def _transaction(self):
        # Connection per call permits safe use across service threads/instances.
        from contextlib import contextmanager

        @contextmanager
        def transaction():
            db = self._connect()
            try:
                db.execute('BEGIN IMMEDIATE')
                yield db
                db.commit()
            except sqlite3.Error:
                db.rollback()
                raise StateError('state_busy_or_full', 503) from None
            except BaseException:
                db.rollback()
                raise
            finally:
                db.close()
        return transaction()

    def _cleanup(self, db, now):
        db.execute('DELETE FROM memory WHERE expires<=?', (now,))
        # Uncertain executions become durable tombstones; expiry cannot rerun them.
        db.execute("UPDATE idempotency SET state='recovery_required',reserved=0 WHERE state='running' AND created<=?",
                   (now - RUN_LEASE_SECONDS,))
        db.execute("DELETE FROM idempotency WHERE state='completed' AND expires<=?", (now,))
        db.execute('''DELETE FROM actors WHERE NOT EXISTS(SELECT 1 FROM memory WHERE memory.actor=actors.actor)
            AND NOT EXISTS(SELECT 1 FROM workflows WHERE workflows.actor=actors.actor)
            AND NOT EXISTS(SELECT 1 FROM idempotency WHERE idempotency.actor=actors.actor)''')

    def _actor(self, db, actor):
        if db.execute('SELECT 1 FROM actors WHERE actor=?', (actor,)).fetchone() is None:
            if db.execute('SELECT COUNT(*) FROM actors').fetchone()[0] >= MAX_ACTORS:
                raise StateError('state_actor_capacity', 429)
            db.execute('INSERT INTO actors VALUES(?)', (actor,))

    def _quotas(self, db, actor):
        count = sum(db.execute('SELECT COUNT(*) FROM ' + table + ' WHERE actor=?', (actor,)).fetchone()[0]
                    for table in ('memory', 'workflows'))
        if count > MAX_RECORDS:
            raise StateError('state_record_capacity', 429)
        actor_bytes, total_bytes = 0, 0
        for table, expression in (('memory', 'length(sealed)'), ('workflows', 'length(sealed)'),
                                  ('idempotency', 'COALESCE(length(result),0)+reserved')):
            actor_bytes += db.execute('SELECT COALESCE(SUM(' + expression + '),0) FROM ' + table + ' WHERE actor=?', (actor,)).fetchone()[0]
            total_bytes += db.execute('SELECT COALESCE(SUM(' + expression + '),0) FROM ' + table).fetchone()[0]
        if actor_bytes > MAX_ACTOR_BYTES or total_bytes > MAX_GLOBAL_BYTES:
            raise StateError('state_storage_capacity', 429)
        if db.execute('SELECT COUNT(*) FROM idempotency WHERE actor=?', (actor,)).fetchone()[0] > MAX_IDEMPOTENCY_ACTOR or db.execute('SELECT COUNT(*) FROM idempotency').fetchone()[0] > MAX_IDEMPOTENCY_GLOBAL:
            raise StateError('idempotency_capacity', 429)

    def _replay(self, db, actor, token, digest):
        row = db.execute('SELECT * FROM idempotency WHERE actor=? AND token=?', (actor, token)).fetchone()
        if row is None:
            return None
        if not hmac.compare_digest(row['digest'], digest):
            raise StateError('idempotency_conflict', 409)
        if row['state'] != 'completed':
            return {'status': 'in_progress' if row['state'] == 'running' else row['state'], 'skill_id': 'workflow.run',
                    'data': {'run_id': row['run_id']}, 'replayed': True}
        result = self._unseal(row['result'], 'replay', actor, token, digest)
        result['replayed'] = True
        return result

    def _cache(self, db, actor, token, digest, result, now, run_id=None):
        _json_bytes(result, MAX_OUTPUT_BYTES)
        sealed = self._seal(result, 'replay', actor, token, digest)
        db.execute('''INSERT INTO idempotency(actor,token,digest,state,result,run_id,created,expires,reserved)
            VALUES(?,?,?,'completed',?,?,?,?,0)''',
            (actor, token, digest, sealed, run_id, now, now + IDEMPOTENCY_TTL))

    def execute(self, skill_id, value, actor: str, idempotency_key=None, runner=None):
        if type(actor) is not str or not _ACTOR.fullmatch(actor):
            raise StateError('invalid_principal', 403)
        normalized = _validate(skill_id, value)
        mutation = skill_id not in ('memory.get', 'memory.search')
        if (mutation or idempotency_key is not None) and (type(idempotency_key) is not str or not _IDEMPOTENCY.fullmatch(idempotency_key)):
            raise StateError('idempotency_required')
        owner = self._hash('actor', actor)
        token = self._hash('idempotency', owner, idempotency_key) if mutation else None
        digest = self._hash('request', owner, skill_id, _json_bytes(value).decode()) if mutation else None
        now = int(time.time())
        if skill_id == 'workflow.run':
            return self._run(normalized, owner, token, digest, now, runner)
        with self._transaction() as db:
            self._cleanup(db, now)
            if mutation:
                replay = self._replay(db, owner, token, digest)
                if replay is not None:
                    return replay
                self._actor(db, owner)
            if skill_id == 'workflow.define':
                workflow_id = str(uuid.uuid4())
                sealed = self._seal(normalized, 'workflow', owner, workflow_id)
                db.execute('INSERT INTO workflows VALUES(?,?,?)', (owner, workflow_id, sealed))
                result = _result(skill_id, {'workflow_id': workflow_id, 'steps': len(normalized['steps'])})
            else:
                scope = value.get('scope', 'default')
                scope_hash = self._hash('scope', owner, scope)
                if skill_id == 'memory.set':
                    key_hash = self._hash('key', owner, scope_hash, value['key'])
                    expires = now + value.get('ttl_seconds', 86400)
                    sealed = self._seal({'key': value['key'], 'value': value['value']},
                                        'memory', owner, scope_hash, key_hash)
                    db.execute('''INSERT INTO memory VALUES(?,?,?,?,?) ON CONFLICT(actor,scope,key)
                        DO UPDATE SET sealed=excluded.sealed,expires=excluded.expires''',
                        (owner, scope_hash, key_hash, sealed, expires))
                    result = _result(skill_id, {'stored': True, 'expires_at': expires})
                elif skill_id == 'memory.get':
                    key_hash = self._hash('key', owner, scope_hash, value['key'])
                    row = db.execute('SELECT sealed,expires FROM memory WHERE actor=? AND scope=? AND key=?',
                                     (owner, scope_hash, key_hash)).fetchone()
                    result = _result(skill_id, {'found': row is not None,
                        'value': self._unseal(row['sealed'], 'memory', owner, scope_hash, key_hash)['value'] if row else None,
                        'expires_at': row['expires'] if row else None})
                elif skill_id == 'memory.clear':
                    count = db.execute('DELETE FROM memory WHERE actor=? AND scope=?', (owner, scope_hash)).rowcount
                    result = _result(skill_id, {'cleared': count})
                else:
                    rows = db.execute('SELECT key,sealed,expires FROM memory WHERE actor=? AND scope=? ORDER BY key',
                                      (owner, scope_hash)).fetchall()
                    needle = value['query'].casefold()
                    matches = []
                    for row in rows:
                        record = self._unseal(row['sealed'], 'memory', owner, scope_hash, row['key'])
                        if needle in record['key'].casefold() or needle in _json_bytes(record['value'], MAX_VALUE_BYTES).decode().casefold():
                            matches.append({**record, 'expires_at': row['expires']})
                    matches.sort(key=lambda item: item['key'])
                    offset, limit = value.get('offset', 0), value.get('limit', 10)
                    items = []
                    for item in matches[offset:offset + limit]:
                        candidate = _result(skill_id, {'items': items + [item], 'total': len(matches),
                            'next_offset': offset + len(items) + 1, 'has_more': True})
                        try:
                            _json_bytes(candidate, MAX_OUTPUT_BYTES - 1024)
                        except StateError as exc:
                            if exc.code not in ('json_size_limit', 'json_complexity'):
                                raise
                            break
                        items.append(item)
                    next_offset = offset + len(items)
                    result = _result(skill_id, {'items': items, 'total': len(matches),
                        'next_offset': next_offset, 'has_more': next_offset < len(matches)})
            if mutation:
                self._cache(db, owner, token, digest, result, now)
                self._quotas(db, owner)
            _json_bytes(result, MAX_OUTPUT_BYTES)
            return result

    def _run(self, value, owner, token, digest, now, runner):
        with self._transaction() as db:
            self._cleanup(db, now)
            replay = self._replay(db, owner, token, digest)
            if replay is not None:
                return replay
            row = db.execute('SELECT sealed FROM workflows WHERE actor=? AND id=?',
                             (owner, value['workflow_id'])).fetchone()
            if row is None:
                raise StateError('workflow_not_found', 404)
            if not callable(runner):
                raise StateError('workflow_runner_required', 503)
            definition = self._unseal(row['sealed'], 'workflow', owner, value['workflow_id'])
            definition = _workflow(definition)  # Revalidate saved policy on every execution.
            run_id = str(uuid.uuid4())
            self._actor(db, owner)
            db.execute('''INSERT INTO idempotency(actor,token,digest,state,run_id,created,expires,reserved)
                VALUES(?,?,?,'running',?,?,?,?)''',
                (owner, token, digest, run_id, now, now + IDEMPOTENCY_TTL, MAX_OUTPUT_BYTES + 64))
            self._quotas(db, owner)
        # No SQLite connection or write lock exists while caller code executes.
        started = time.monotonic()
        results, failed = [], False
        for step in definition['steps']:
            if failed:
                results.append({'id': step['id'], 'status': 'skipped'})
                continue
            if time.monotonic() - started >= RUN_BUDGET_SECONDS:
                results.append({'id': step['id'], 'status': 'failed', 'error': 'workflow_deadline'})
                failed = True
                continue
            try:
                output = runner(step['skill_id'], copy.deepcopy(step['input']))
                _json_bytes(output, MAX_OUTPUT_BYTES - 8192, max_depth=12, max_nodes=10000)
                if type(output) is not dict:
                    raise StateError('invalid_step_result')
                success = output.get('status') == 'completed' and output.get('analysis_performed') is not False
                entry = {'id': step['id'], 'status': 'completed' if success else 'failed', 'result': output}
                if time.monotonic() - started >= RUN_BUDGET_SECONDS:
                    entry['status'], entry['error'] = 'failed', 'workflow_deadline'
                    success = False
                _json_bytes(results + [entry], MAX_OUTPUT_BYTES - 8192,
                            max_depth=16, max_nodes=16000)
            except Exception:
                entry = {'id': step['id'], 'status': 'failed', 'error': 'step_failed_or_output_limit'}
                success = False
            results.append(entry)
            failed = not success
        result = _result('workflow.run', {'workflow_id': value['workflow_id'], 'run_id': run_id,
            'status': 'failed' if failed else 'completed', 'steps': results,
            'external_actions_performed': False})
        encoded = _json_bytes(result, MAX_OUTPUT_BYTES)
        with self._transaction() as db:
            row = db.execute('SELECT state FROM idempotency WHERE actor=? AND token=? AND run_id=?',
                             (owner, token, run_id)).fetchone()
            if row is None or row['state'] != 'running':
                return {'status': 'recovery_required', 'skill_id': 'workflow.run',
                        'data': {'run_id': run_id}, 'replayed': False}
            # The upfront reservation guarantees sufficient logical result capacity.
            sealed = self._seal(json.loads(encoded), 'replay', owner, token, digest)
            db.execute("UPDATE idempotency SET state='completed',result=?,reserved=0,expires=? WHERE actor=? AND token=? AND run_id=? AND state='running'",
                       (sealed, int(time.time()) + IDEMPOTENCY_TTL, owner, token, run_id))
            self._quotas(db, owner)
        return result

    def close(self):
        """No persistent connection is held; close prevents future operations."""
        self._closed = True
