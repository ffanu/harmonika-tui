"""Private native-TUI adapter: metadata, explicit project notes, existing job control.

Never reads conversation history, raw backend logs, native databases or provider keys.
Only the gateway bearer file is read, and its value never enters argv or output.
"""
import concurrent.futures
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import stat
import sys
import time
import urllib.error
import urllib.request

ORIGIN = 'https://chat.harmonika.id'
SOURCES = {'coding': '/v1/coding', 'unified': '/v1/unified', 'media': '/v1/media'}
STATES = frozenset(('queued', 'running', 'completed', 'failed', 'cancelled', 'stopped', 'unknown', 'cancel_requested', 'recovery_required', 'in_progress', 'approval_required', 'timeout', 'expired', 'interrupted'))
GRAPHICS = frozenset(('online', 'offline', 'busy', 'vram_full', 'warming', 'timeout', 'bad_payload', 'auth_failed', 'recovery_required', 'unavailable', 'unknown', 'not_checked'))
ERRORS = frozenset(('forbidden', 'unauthorized', 'token_expired', 'rate_limit', 'quota_exceeded', 'core_busy', 'in_progress', 'recovery_required', 'idempotency_conflict', 'sensitive_content_rejected', 'consent_required', 'invalid_input_fields', 'invalid_project_id', 'invalid_key', 'invalid_text', 'invalid_kind', 'not_found', 'job_not_found', 'invalid_job_id', 'project_scope_reserved', 'idempotency_required'))
IDENTIFIER = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}')
PROJECT = re.compile(r'tui-[a-f0-9]{40}')


class ControlError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise ControlError('redirect_refused')


def config_directory():
    return Path(os.environ.get('HARMONIKA_GLOBAL_CONFIG') or os.environ.get('HARMONIKA_CONFIG_DIR') or Path.home()/'.config/harmonika')


def private_key():
    configured = os.environ.get('HARMONIKA_API_KEY', '').strip()
    if configured:
        if not re.fullmatch(r'hai_[A-Za-z0-9_-]{16,240}', configured):
            raise ControlError('invalid_key_file')
        return configured
    directory = config_directory()
    path = directory/'harmonika-api-key'
    if directory.is_symlink() or path.is_symlink():
        raise ControlError('unsafe_key_file')
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0))
        with os.fdopen(fd) as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or (os.name != 'nt' and (info.st_uid != os.getuid() or info.st_mode & 0o077)):
                raise ControlError('unsafe_key_file')
            key = stream.read(257).strip()
    except OSError:
        raise ControlError('key_not_configured') from None
    if not re.fullmatch(r'hai_[A-Za-z0-9_-]{16,240}', key):
        raise ControlError('invalid_key_file')
    return key


def route_allowed(method, route):
    if method == 'GET' and route in ('/v1/core/capabilities', '/v1/skills/discovery', '/v1/media/capabilities'):
        return True
    if method == 'POST' and route == '/v1/core/invoke':
        return True
    for base in SOURCES.values():
        if re.fullmatch(re.escape(base)+r'/jobs/[A-Za-z0-9_-]{1,128}', route) and method == 'GET':
            return True
    if method == 'POST' and re.fullmatch(r'/v1/(?:coding|unified)/jobs/[A-Za-z0-9_-]{1,128}/stop', route):
        return True
    if method == 'POST' and re.fullmatch(r'/v1/media/jobs/job_[A-Za-z0-9_-]{1,128}/cancel', route):
        return True
    return False


def request(method, route, payload=None, idem=None, *, timeout=3):
    if not route_allowed(method, route):
        raise ControlError('unsupported_operation')
    key = private_key()
    mutation = method == 'POST' and not (route == '/v1/core/invoke' and type(payload) is dict and payload.get('skill_id') in ('memory.get', 'memory.search'))
    headers = {'Authorization': 'Bearer '+key, 'Content-Type': 'application/json'}
    # Share the identity created by license activation, never mint a different
    # device binding from the plugin. Do not expose it in output or argv.
    device = os.environ.get('HARMONIKA_DEVICE_ID', '')
    if not device:
        try:
            device = (config_directory()/'device-id').read_text(encoding='utf-8').strip()
        except OSError:
            pass
    if re.fullmatch(r'dev_[a-f0-9]{32}', device):
        headers['X-Harmonika-Device-ID'] = device
    name = os.environ.get('HARMONIKA_DEVICE_NAME', '')
    if name and len(name) <= 72 and all(32 <= ord(c) <= 126 for c in name):
        headers['X-Harmonika-Device-Name'] = name
    if idem is not None:
        if type(idem) is not str or not re.fullmatch(r'[A-Za-z0-9_-]{8,128}', idem):
            raise ControlError('idempotency_required')
        headers['Idempotency-Key'] = idem
    body = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode() if payload is not None else None
    if body and len(body) > 32768:
        raise ControlError('input_too_large')
    req = urllib.request.Request(ORIGIN+route, data=body, method=method, headers=headers)
    try:
        with urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect()).open(req, timeout=timeout) as response:
            raw = response.read(262145)
    except urllib.error.HTTPError as error:
        if error.code in (401, 403):
            raise ControlError('auth_required') from None
        if mutation and error.code >= 500:
            raise ControlError('submission_unknown') from None
        raw = error.read(65536)
        try:
            obj = json.loads(raw)
            code = obj.get('error', {}).get('code')
        except (ValueError, AttributeError):
            code = None
        raise ControlError(code if code in ERRORS else 'rate_limit' if error.code == 429 else 'gateway_unavailable') from None
    except ControlError:
        raise
    except Exception:
        # A timed-out mutation is uncertain. Never retry it with a new key.
        raise ControlError('submission_unknown' if mutation else 'offline') from None
    if len(raw) > 262144:
        raise ControlError('submission_unknown' if mutation else 'bad_payload')
    try:
        value = json.loads(raw)
    except (ValueError, RecursionError):
        raise ControlError('submission_unknown' if mutation else 'bad_payload') from None
    if type(value) is not dict:
        raise ControlError('submission_unknown' if mutation else 'bad_payload')
    if value.get('error'):
        error = value['error'];code = error.get('code') if type(error) is dict else None
        raise ControlError(code if code in ERRORS else 'gateway_unavailable')
    return value


def project_id(directory):
    return 'tui-'+hashlib.sha256(str(Path(directory).resolve()).encode()).hexdigest()[:40]


def status():
    def probe(route):
        began = time.monotonic()
        try:
            return {'value': request('GET', route, timeout=5 if route.endswith('/discovery') else 3), 'latency_ms': round((time.monotonic()-began)*1000), 'status': 'online'}
        except ControlError as error:
            return {'status': error.code}
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        core, discovery = list(pool.map(probe, ('/v1/core/capabilities', '/v1/skills/discovery')))
    value = discovery.get('value', {})
    diagnostics = value.get('diagnostics', {})
    graphics = diagnostics.get('graphics', {}) if type(diagnostics) is dict else {}
    out = {'ok': core['status'] == 'online', 'checked_at': time.time(),
           'backend': {'status': core['status'], 'latency_ms': core.get('latency_ms')},
           'graphics': {'status': graphics.get('status') if graphics.get('status') in GRAPHICS else 'unknown'},
           'registry': {'status': value.get('status') if value.get('status') in ('ready', 'degraded', 'disabled', 'scanning', 'recovering') else 'unavailable'}}
    if type(value.get('registered')) is int:
        out['registry']['registered'] = value['registered']
    return out


def job_metadata(source, job_id, value):
    job = value.get('job', value.get('data', value))
    if type(job) is not dict:
        raise ControlError('bad_payload')
    returned = job.get('job_id', job.get('id'))
    if returned != job_id:
        raise ControlError('job_identity_mismatch')
    state = job.get('status', job.get('state'))
    if state not in STATES:
        raise ControlError('bad_payload')
    sequence = job.get('sequence', 0)
    if type(sequence) is not int or not 0 <= sequence <= 100000000:
        sequence = 0
    progress = job.get('progress')
    if type(progress) not in (int, float) or not math.isfinite(progress) or not 0 <= progress <= 100:
        progress = None
    exit_code = None
    if source == 'coding' and job.get('skill_id') in ('test.run', 'test.single', 'test.coverage', 'build.run', 'exec.shell', 'exec.python', 'exec.node', 'exec.binary'):
        result = job.get('result')
        code = result.get('exit_code') if type(result) is dict else None
        if type(code) is int and -255 <= code <= 255:
            exit_code = code
    return {'source': source, 'job_id': job_id, 'status': state, 'sequence': sequence, 'progress': progress, 'exit_code': exit_code, 'checked_at': time.time(), 'connection': 'online', 'can_stop': state in ('queued', 'running', 'approval_required')}


class Jobs:
    def __init__(self, root=None):
        self.root = Path(root or Path(os.environ.get('XDG_STATE_HOME', str(Path.home()/'.local/state')))/'harmonika-terminal/control')
        if self.root.is_symlink():
            raise ControlError('unsafe_state_path')
        self.root.mkdir(parents=True, mode=0o700, exist_ok=True)
        if os.name != 'nt' and (self.root.stat().st_uid != os.getuid() or self.root.stat().st_mode & 0o077):
            raise ControlError('unsafe_state_path')
        self.path = self.root/'jobs.sqlite3'
        if self.path.exists() and (self.path.is_symlink() or self.path.stat().st_nlink != 1 or os.name != 'nt' and (self.path.stat().st_uid != os.getuid() or self.path.stat().st_mode & 0o077)):
            raise ControlError('unsafe_state_path')
        if not self.path.exists():
            fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600);os.close(fd)
        self.actor = hashlib.sha256(private_key().encode()).hexdigest()
        with self.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS jobs(actor TEXT, project TEXT, source TEXT, id TEXT, metadata TEXT, checked REAL, PRIMARY KEY(actor,project,source,id))')
            db.execute('DELETE FROM jobs WHERE checked<?', (time.time()-86400,))

    def connect(self):
        return sqlite3.connect(str(self.path), timeout=2)

    def list(self, project):
        with self.connect() as db:
            return [json.loads(row[0]) for row in db.execute('SELECT metadata FROM jobs WHERE actor=? AND project=? ORDER BY checked DESC LIMIT 20', (self.actor, project))]

    def save(self, project, value):
        # Only these mechanics may ever be persisted, even when an API adds fields.
        value = {k: value[k] for k in ('source', 'job_id', 'status', 'sequence', 'progress', 'exit_code', 'checked_at', 'connection', 'can_stop')}
        with self.connect() as db:
            existing = db.execute('SELECT 1 FROM jobs WHERE actor=? AND project=? AND source=? AND id=?', (self.actor, project, value['source'], value['job_id'])).fetchone()
            if not existing and db.execute('SELECT count(*) FROM jobs').fetchone()[0] >= 1000:
                raise ControlError('job_cache_capacity')
            db.execute('INSERT INTO jobs VALUES(?,?,?,?,?,?) ON CONFLICT(actor,project,source,id) DO UPDATE SET metadata=excluded.metadata,checked=excluded.checked', (self.actor, project, value['source'], value['job_id'], json.dumps(value), value['checked_at']))


def job_action(action, args, store):
    source = args.get('source');jid = args.get('job_id')
    if source not in SOURCES or type(jid) is not str or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', jid):
        raise ControlError('invalid_job_id')
    route = SOURCES[source]+'/jobs/'+jid
    # Reconcile actual current status before a stop; stop never creates a job.
    current = job_metadata(source, jid, request('GET', route))
    if action == 'stop_job':
        if args.get('confirmed') is not True:
            raise ControlError('confirmation_required')
        if not current['can_stop']:
            raise ControlError('job_stop_unavailable')
        current = job_metadata(source, jid, request('POST', route+('/cancel' if source == 'media' else '/stop'), {}))
    store.save(args['project_id'], current)
    return {'ok': True, 'job': current}


def memory(args):
    operation = args.get('operation')
    fields = {'get': {'key'}, 'search': {'query', 'limit', 'offset'}, 'set': {'key', 'value', 'consent', 'ttl_seconds'}, 'clear': {'all'}}
    if operation not in fields or set(args)-{'action', 'operation', 'project_id', 'idempotency_key'}-fields[operation]:
        raise ControlError('invalid_memory_request')
    if operation == 'set' and args.get('consent') is not True or operation == 'clear' and args.get('all') is not True:
        raise ControlError('confirmation_required')
    if operation in ('set', 'clear') and not re.fullmatch(r'[A-Za-z0-9_-]{8,128}', args.get('idempotency_key', '')):
        raise ControlError('idempotency_required')
    value = {k: v for k, v in args.items() if k in fields[operation] or k == 'project_id'}
    result = request('POST', '/v1/core/invoke', {'skill_id': 'memory.'+operation, 'input': value}, args.get('idempotency_key'), timeout=8)
    if result.get('status') != 'completed' or result.get('skill_id') != 'memory.'+operation or type(result.get('data')) is not dict:
        raise ControlError('submission_unknown' if operation in ('set', 'clear') else 'bad_payload')
    return {'ok': True, 'untrusted_data': True, 'data': result['data'], 'replayed': result.get('replayed', False)}


def audit_local():
    try:
        from tui_config_audit import audit_config
    except ModuleNotFoundError:
        import importlib.util
        spec = importlib.util.spec_from_file_location('tui_control_config_audit', Path(__file__).resolve().with_name('tui_config_audit.py'))
        module = importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        audit_config = module.audit_config
    root = config_directory()
    reports = []
    for name in ('harmonika.json', 'opencode.json', 'cli.json'):
        path = root/name
        if not path.exists():
            continue
        if path.is_symlink() or path.stat().st_size > 524288:
            raise ControlError('unsafe_config_file')
        with path.open('r') as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or os.name != 'nt' and (info.st_uid != os.getuid() or info.st_mode & 0o077):
                raise ControlError('unsafe_config_file')
            value = json.loads(stream.read(524289))
        reports.append({'source': name, **audit_config(value)})
    return {'ok': True, 'audit_only': True, 'reports': reports}


def dispatch(args, *, jobs_factory=Jobs):
    if type(args) is not dict or type(args.get('action')) is not str:
        raise ControlError('invalid_request')
    action = args['action']
    if action == 'status' and set(args) == {'action'}:
        return status()
    if action == 'audit_config' and set(args) == {'action'}:
        return audit_local()
    if not PROJECT.fullmatch(args.get('project_id', '')):
        raise ControlError('invalid_project_id')
    if action == 'memory':
        return memory(args)
    if action not in ('jobs', 'track_job', 'stop_job'):
        raise ControlError('unsupported_operation')
    allowed = {'action', 'project_id'} | ({'source', 'job_id', 'confirmed'} if action != 'jobs' else set())
    if set(args)-allowed:
        raise ControlError('invalid_request')
    store = jobs_factory()
    if action != 'jobs':
        return job_action(action, args, store)
    rows = store.list(args['project_id'])
    # Poll the oldest four active jobs in parallel; never serialize 20 deadlines.
    active = sorted((row for row in rows if row['status'] in ('queued', 'running', 'in_progress', 'approval_required', 'cancel_requested', 'unknown', 'recovery_required')), key=lambda row: row['checked_at'])[:4]
    def refresh(row):
        try:
            return job_action('track_job', {**row, 'project_id': args['project_id']}, store)['job']
        except ControlError as error:
            return {**row, 'connection': error.code, 'can_stop': False}
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        refreshed = {(row['source'], row['job_id']): row for row in pool.map(refresh, active)}
    out = [refreshed.get((row['source'], row['job_id']), row) for row in rows]
    return {'ok': True, 'jobs': out, 'only_tracked_jobs': True}


def main():
    try:
        raw = sys.stdin.buffer.read(32769)
        if len(raw) > 32768:
            raise ControlError('input_too_large')
        result = dispatch(json.loads(raw))
    except ControlError as error:
        result = {'ok': False, 'error': error.code, 'retry_submission': False}
    except Exception:
        result = {'ok': False, 'error': 'control_unavailable', 'retry_submission': False}
    print(json.dumps(result, ensure_ascii=False, allow_nan=False))


if __name__ == '__main__':
    main()
