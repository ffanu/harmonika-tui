"""Compatibility transport for qualified Wan 2.2; old owned artifacts remain readable."""
import base64
import importlib.util
import json
import os
from pathlib import Path
import hashlib
import queue
import re
import secrets
import shutil
import subprocess
import sys
import time
import urllib.request

CONTROL = Path(__file__).resolve().parent
LEGACY_CONTROL = CONTROL.parent/'harmonika-video-toolkit-source'
JOBS = Path(r'C:\HarmonikaVideo\jobs')
PORT = 18261


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


wf = load('quality_workflow', CONTROL/'workflow.py')


def validate(payload):
    if not isinstance(payload, dict) or set(payload)-{'kind', 'prompt', 'duration_seconds', 'image_base64'}:
        raise ValueError('invalid_input')
    kind = payload.get('kind'); seconds = payload.get('duration_seconds', 1)
    if (kind not in {'video.generate', 'video.animate_image'} or type(seconds) is not int or not 1 <= seconds <= 10
            or not isinstance(payload.get('prompt'), str) or not payload['prompt'].strip()
            or len(payload['prompt'].encode()) > 8000):
        raise ValueError('unsupported_profile')
    if kind == 'video.animate_image':
        try:
            raw = base64.b64decode(payload.get('image_base64', ''), validate=True)
            if not 100 <= len(raw) <= 8*1024**2:
                raise ValueError()
            if not (raw.startswith(b'\x89PNG\r\n\x1a\n') or raw.startswith(b'\xff\xd8\xff') or (raw.startswith(b'RIFF') and raw[8:12] == b'WEBP')):
                raise ValueError()
        except (ValueError, TypeError):
            raise ValueError('invalid_image') from None
    elif payload.get('image_base64'):
        raise ValueError('unsupported_reference')
    try:
        p = wf.active_profile(kind)
        if seconds > p['max_segment_seconds']:
            raise wf.Failure('segment_too_long')
    except wf.Failure:
        raise ValueError('engine_not_qualified') from None
    return {**payload, 'duration_seconds': seconds}


def runtime_ready():
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open('http://127.0.0.1:18193/system_stats', timeout=2) as response:
            return response.status == 200
    except OSError:
        return False


def capabilities():
    engines = {}; profiles = {}
    available = runtime_ready()
    for kind, alias in [('video.generate', 'wangp'), ('video.animate_image', 'framepack')]:
        try:
            p = wf.active_profile(kind); ready = available
            if ready:
                profiles[kind] = p
        except wf.Failure:
            p = {}; ready = False
        # Alias keys preserve the old owner RPC contract, not a model identity claim.
        engines[alias] = {'operator_ready': ready, 'models_verified': ready, 'inference_verified': ready,
                          'qualified_duration_seconds': list(range(1, p.get('max_segment_seconds', 0)+1)),
                          'model': p.get('model'), 'public_route_enabled': False}
    return {'service': 'Harmonika Ai', 'audience': 'private_operator_only', 'engines': engines,
            'quality_profiles': profiles, 'quality': 'high', 'runtime_ready': available, 'preview_fallback': False}


class QualityLifecycle:
    """Resource admission counts active work; segment count is not a daily quota."""
    def __init__(self):
        super().__init__()
        for row in self.db.execute("SELECT id FROM jobs WHERE state='unknown'").fetchall():
            for name in ('input.json', 'input.png'):
                (JOBS/row['id']/name).unlink(missing_ok=True)

    def submit(self, owner, idem, payload):
        data = validate(payload)
        digest = hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()
        with self.lock:
            old = self.db.execute('SELECT * FROM jobs WHERE owner=? AND idem=?', (owner, idem)).fetchone()
            if old:
                if old['digest'] != digest:
                    raise ValueError('idempotency_conflict')
                return dict(old)
            if self.db.execute("SELECT 1 FROM jobs WHERE state='unknown'").fetchone():
                raise ValueError('engine_recovery_required')
            if shutil.disk_usage(JOBS).free < 2*1024**3:
                raise ValueError('storage_full')
            if (self.pending.full() or self.db.execute("SELECT count(*) FROM jobs WHERE state IN ('queued','running')").fetchone()[0] >= 4
                    or self.db.execute("SELECT 1 FROM jobs WHERE owner=? AND state IN ('queued','running')", (owner,)).fetchone()):
                raise ValueError('queue_full')
            jid = secrets.token_hex(16); now = time.time()
            self.db.execute('INSERT INTO jobs VALUES(?,?,?,?,?,?,?,?,?,?)', (jid, owner, idem, digest, 'queued', now, now, None, None, None))
            self.db.commit(); self.pending.put_nowait((jid, data))
            return self.row(owner, jid)

    def expire(self):
        with self.lock:
            rows = self.db.execute("SELECT id FROM jobs WHERE state IN ('completed','failed') AND created<? AND error_type IS NOT 'artifact_expired'", (time.time()-86400,)).fetchall()
            for row in rows:
                if not re.fullmatch('[a-f0-9]{32}', row['id']):
                    continue
                job = JOBS/row['id']
                if job.is_symlink():
                    continue
                if job.is_dir():
                    shutil.rmtree(job)
                # Retain idempotency tombstones; an expired key cannot create new work.
                self.db.execute("UPDATE jobs SET state='failed',error_type='artifact_expired',result=NULL WHERE id=?", (row['id'],))
            self.db.commit()


def main():
    legacy = load('legacy_video_broker', LEGACY_CONTROL/'broker_av.py')
    containment_module = load('owned_quality_job', LEGACY_CONTROL/'owned_job.py')
    # Dedicated listener/ledger/lock preserve existing legacy jobs and processes.
    legacy.CONTROL = CONTROL
    class QualityHTTPServer(legacy.ThreadingHTTPServer):
        def __init__(self, address, handler):
            super().__init__(('127.0.0.1', PORT), handler)
    legacy.ThreadingHTTPServer = QualityHTTPServer

    class QualityBroker(QualityLifecycle, legacy.Broker):
        def worker(self):
            sweep = 0
            while True:
                if time.monotonic()-sweep > 3600:
                    try:
                        self.expire()
                    except OSError:
                        print(json.dumps({'event': 'quality_retention_failed', 'error_type': 'artifact_cleanup_failed'}), flush=True)
                    sweep = time.monotonic()
                try:
                    jid, data = self.pending.get(timeout=60)
                except queue.Empty:
                    continue
                start = time.monotonic(); job = legacy.JOBS/jid
                state = 'failed'; error = None; result = None; guard = None; containment = None; child = None
                with self.lock:
                    self.db.execute("UPDATE jobs SET state='running',updated=? WHERE id=?", (time.time(), jid)); self.db.commit()
                try:
                    import msvcrt
                    p = wf.active_profile(data['kind'])
                    job.mkdir(parents=True)
                    guard = (LEGACY_CONTROL/'gpu.lock').open('a+b')
                    guard.seek(0); guard.write(b'0'); guard.flush(); guard.seek(0)
                    try:
                        msvcrt.locking(guard.fileno(), msvcrt.LK_NBLCK, 1)
                    except OSError:
                        raise wf.Failure('gpu_busy') from None
                    payload = {'prompt': data['prompt'], 'duration_seconds': data['duration_seconds'],
                               'kind': data['kind'], 'family': p['family']}
                    (job/'input.json').write_text(json.dumps(payload))
                    if data['kind'] == 'video.animate_image':
                        (job/'input.png').write_bytes(base64.b64decode(data['image_base64'], validate=True))
                    env = {k: v for k, v in os.environ.items() if not any(s in k.upper() for s in ['SECRET', 'TOKEN', 'PASSWORD', 'API_KEY'])}
                    child = subprocess.Popen([sys.executable, '-I', str(CONTROL/'runner.py'), str(job)],
                                             env=env, cwd=CONTROL, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                             stderr=subprocess.DEVNULL, creationflags=0x4)
                    containment = containment_module.WindowsJob(child)
                    while child.poll() is None:
                        if time.monotonic()-start > 2400:
                            raise wf.Failure('quality_timeout_uncertain')
                        time.sleep(2)
                    result = json.loads((job/'result.json').read_text())
                    if result.get('status') != 'completed':
                        raise wf.Failure(result.get('error_code', 'quality_inference_failed'))
                    state = 'completed'
                except Exception as exc:
                    error = str(exc) if isinstance(exc, wf.Failure) else 'quality_inference_failed'
                    state = 'unknown' if error.endswith('_uncertain') or error == 'comfy_reconciliation_required' else 'failed'
                    result = None
                finally:
                    if containment:
                        containment.close()
                    if child is not None and child.poll() is None:
                        child.kill(); child.wait(10)
                    if guard:
                        guard.close()
                    try:
                        for name in ['input.json', 'input.png']:
                            (job/name).unlink(missing_ok=True)
                    except OSError:
                        error = 'input_cleanup_failed'
                        if state != 'unknown':
                            state = 'failed'
                        result = None
                with self.lock:
                    self.db.execute('UPDATE jobs SET state=?,updated=?,error_type=?,latency=?,result=? WHERE id=?',
                                    (state, time.time(), error, round(time.monotonic()-start, 2), json.dumps(result) if result else None, jid)); self.db.commit()
                print(json.dumps({'event': 'quality_job_terminal', 'state': state, 'error_type': error,
                                  'latency': round(time.monotonic()-start, 2)}), flush=True)
                self.pending.task_done()

    legacy.validate = validate; legacy.capabilities = capabilities; legacy.Broker = QualityBroker
    legacy.main()


if __name__ == '__main__':
    main()
