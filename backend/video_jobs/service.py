"""Loopback authenticated async video queue. SQLite contains mechanics only.
Prompt remains in bounded RAM, never in audit/history; restart marks unfinished unknown.
"""
import hashlib, hmac, json, os, re, secrets, sqlite3, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import importlib.util
_spec = importlib.util.spec_from_file_location('harmonika_video_renderer', Path(__file__).with_name('renderer.py'))
_renderer = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_renderer)
render, RenderError = _renderer.render, _renderer.RenderError

MAX_BODY = 16384
ID = re.compile(r'^[a-f0-9]{32}$')
OWNER = re.compile(r'^[a-f0-9]{64}$')


def validate(data):
    if not isinstance(data, dict) or set(data) - {'prompt', 'duration_seconds'}:
        raise ValueError('invalid_input')
    prompt = data.get('prompt')
    duration = data.get('duration_seconds', 10)
    if (not isinstance(prompt, str) or not prompt.strip() or len(prompt.encode()) > 8000
            or type(duration) is not int or duration != 10):
        raise ValueError('invalid_input')
    return {'prompt': prompt.strip(), 'duration_seconds': duration}


class Store:
    def __init__(self, folder, engine_root, token, runner=render):
        self.folder, self.engine_root, self.token, self.runner = Path(folder), Path(engine_root), token, runner
        self.folder.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.db = sqlite3.connect(self.folder/'jobs.sqlite3', check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, owner TEXT, idem TEXT, digest TEXT, state TEXT, stage TEXT, created REAL, updated REAL, error_type TEXT, latency REAL, prompt_id TEXT, result TEXT, UNIQUE(owner,idem))')
        self.db.execute("UPDATE jobs SET state='unknown',stage='unknown',error_type='worker_restarted' WHERE state IN ('queued','running')")
        self.db.commit()
        self.lock = threading.RLock()
        self.wake = threading.Event()
        self.pending = {}
        self.active = False
        self.halted = bool(self.db.execute("SELECT 1 FROM jobs WHERE state='unknown' LIMIT 1").fetchone())

    def row(self, owner, job_id):
        with self.lock:
            row = self.db.execute('SELECT * FROM jobs WHERE id=? AND owner=?', (job_id, owner)).fetchone()
            return dict(row) if row else None

    def submit(self, owner, idem, payload):
        payload = validate(payload)
        digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
        with self.lock:
            old = self.db.execute('SELECT * FROM jobs WHERE owner=? AND idem=?', (owner, idem)).fetchone()
            if old:
                if not hmac.compare_digest(old['digest'], digest):
                    raise ValueError('idempotency_conflict')
                return dict(old)
            if self.halted:
                raise ValueError('engine_recovery_required')
            if self.db.execute('SELECT count(*) FROM jobs').fetchone()[0] >= 1000:
                raise ValueError('storage_full')
            if len(self.pending) >= 4 or self.db.execute("SELECT 1 FROM jobs WHERE owner=? AND state IN ('queued','running')", (owner,)).fetchone():
                raise ValueError('queue_full')
            if self.db.execute('SELECT count(*) FROM jobs WHERE owner=? AND created>?', (owner, time.time()-86400)).fetchone()[0] >= 10:
                raise ValueError('daily_limit')
            total = 0
            for path in self.folder.glob('*.mp4'):
                try:total += path.stat().st_size
                except OSError:continue
            if total > 512*1024*1024:
                raise ValueError('storage_full')
            job_id, now = secrets.token_hex(16), time.time()
            self.db.execute('INSERT INTO jobs VALUES (?,?,?,?,?,?,?,?,?,?,?,?)', (job_id,owner,idem,digest,'queued','queued',now,now,None,None,None,None))
            self.db.commit()
            self.pending[job_id] = payload
            self.wake.set()
            return self.row(owner, job_id)

    @staticmethod
    def public(row):
        return {key: row[key] for key in ('id','state','stage','error_type','latency')} | {'result': json.loads(row['result']) if row['result'] else None}

    def by_request(self, owner, idem):
        with self.lock:
            row = self.db.execute('SELECT * FROM jobs WHERE owner=? AND idem=?', (owner,idem)).fetchone()
            return dict(row) if row else None

    def prune(self):
        with self.lock:
            rows = self.db.execute("SELECT id FROM jobs WHERE state IN ('completed','failed') AND updated<? LIMIT 64", (time.time()-7*86400,)).fetchall()
            for row in rows:
                job_id = row['id']
                try:
                    (self.folder/(job_id+'.mp4')).unlink(missing_ok=True)
                    (self.folder/(job_id+'.partial.mp4')).unlink(missing_ok=True)
                    base = self.engine_root/'ComfyUI/output/harmonika-web-video'
                    for path in base.glob(job_id+'_*.mp4'):
                        if path.is_file() and not path.is_symlink():path.unlink()
                except OSError:
                    continue
                self.db.execute('DELETE FROM jobs WHERE id=?', (job_id,))
            self.db.commit()

    def loop(self):
        while True:
            self.wake.wait(2)
            self.prune()
            with self.lock:
                if self.halted or not self.pending:
                    self.wake.clear()
                    continue
                job_id = next(iter(self.pending))
                payload = self.pending.pop(job_id)
                self.active = True
                self.db.execute("UPDATE jobs SET state='running',stage='checking_engine',updated=? WHERE id=?", (time.time(), job_id))
                self.db.commit()
            start = time.monotonic()
            def update(stage, prompt_id=None):
                with self.lock:
                    self.db.execute('UPDATE jobs SET stage=?,prompt_id=COALESCE(?,prompt_id),updated=? WHERE id=?', (stage,prompt_id,time.time(),job_id))
                    self.db.commit()
            try:
                result = self.runner(self.engine_root, self.folder, job_id, payload, update)
                status, error = 'completed', None
            except Exception as exc:
                error = str(exc) if isinstance(exc, RenderError) else 'engine_failed'
                if not re.fullmatch('[a-z_]{1,64}', error):
                    error = 'engine_failed'
                status = 'unknown' if error.endswith('_unknown') else 'failed'
                result = None
            with self.lock:
                if status == 'unknown':
                    self.halted = True
                    self.pending.clear()
                    self.db.execute("UPDATE jobs SET state='failed',stage='failed',error_type='engine_recovery_required' WHERE state='queued'")
                self.db.execute('UPDATE jobs SET state=?,stage=?,error_type=?,latency=?,result=?,updated=? WHERE id=?',
                                (status,status,error,round(time.monotonic()-start,2),json.dumps(result) if result else None,time.time(),job_id))
                self.db.commit()
                self.active = False
            # No raw exception, prompt, URL, token, media or customer fields in logs.
            print(json.dumps({'event':'video_job_terminal','state':status,'error_type':error,'latency':round(time.monotonic()-start,2)}), flush=True)


def handler(store):
    class Handler(BaseHTTPRequestHandler):
        timeout = 20
        def log_message(self, *args):
            pass
        def reply(self, code, data):
            raw = json.dumps(data).encode()
            self.send_response(code)
            self.send_header('Content-Type','application/json')
            self.send_header('Content-Length',str(len(raw)))
            self.send_header('Cache-Control','no-store')
            self.end_headers()
            self.wfile.write(raw)
        def authorized(self):
            auth = self.headers.get('Authorization','')
            owner = self.headers.get('X-Resource-Owner','')
            if not hmac.compare_digest(auth.encode('utf-8'), ('Bearer '+store.token).encode('ascii')) or not OWNER.fullmatch(owner):
                self.reply(401, {'error':'unauthorized'})
                return None
            return owner
        def do_POST(self):
            owner = self.authorized()
            if owner is None:
                return
            if self.path != '/jobs':
                return self.reply(404, {'error':'not_found'})
            try:
                size = int(self.headers.get('Content-Length','0'))
                idem = self.headers.get('Idempotency-Key','')
                if not 1 <= size <= MAX_BODY or not ID.fullmatch(idem):
                    raise ValueError('invalid_input')
                row = store.submit(owner, idem, json.loads(self.rfile.read(size)))
                self.reply(202, store.public(row))
            except (ValueError, UnicodeError, OSError) as exc:
                code = str(exc) if str(exc) in {'invalid_input','queue_full','daily_limit','storage_full','engine_recovery_required','idempotency_conflict'} else 'invalid_input'
                self.reply(409 if code=='idempotency_conflict' else 400 if code=='invalid_input' else 429 if code=='daily_limit' else 503, {'error':code})
        def do_GET(self):
            owner = self.authorized()
            if owner is None:
                return
            if self.path == '/health':
                return self.reply(200, {'service':'video_jobs','halted':store.halted,'active':store.active,'preview':True})
            lookup = re.fullmatch(r'/requests/([a-f0-9]{32})',self.path)
            if lookup:
                row = store.by_request(owner, lookup[1])
                return self.reply(200, store.public(row)) if row else self.reply(404, {'error':'not_found'})
            match = re.fullmatch(r'/jobs/([a-f0-9]{32})(/file)?',self.path)
            row = store.row(owner, match[1]) if match else None
            if not row:
                return self.reply(404, {'error':'not_found'})
            if not match[2]:
                return self.reply(200, store.public(row))
            path = store.folder/(match[1]+'.mp4')
            if row['state'] != 'completed' or not path.is_file():
                return self.reply(404, {'error':'not_found'})
            try:
                handle = path.open('rb')
            except OSError:
                return self.reply(503, {'error':'artifact_unavailable'})
            self.send_response(200)
            self.send_header('Content-Type','video/mp4')
            self.send_header('Content-Length', str(path.stat().st_size))
            self.send_header('Cache-Control','private,no-store')
            self.end_headers()
            with handle as file:
                while chunk := file.read(65536):
                    self.wfile.write(chunk)
    return Handler


if __name__ == '__main__':
    root = Path(os.environ['HARMONIKA_VIDEO_ROOT'])
    token = (root/'service.token').read_text().strip()
    if len(token) < 40:
        raise SystemExit('invalid_service_token')
    store = Store(root/'artifacts', root.parent/'harmonika-h3-worker', token)
    threading.Thread(target=store.loop,daemon=True).start()
    class PrivateServer(ThreadingHTTPServer):
        def handle_error(self, request, client_address):
            # No paths, request details, IPs or raw tracebacks in technical logs.
            print(json.dumps({'event':'video_http_error','error_type':'handler_failed'}),flush=True)
    server = PrivateServer(('127.0.0.1',18233),handler(store))
    server.daemon_threads=True
    server.serve_forever()
