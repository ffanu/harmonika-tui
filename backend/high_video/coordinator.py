"""Owner-scoped long video assembly at native qualified quality, never 288p."""
import base64
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import shutil
import subprocess
import time
import urllib.error
import urllib.request

CONTROL = Path(__file__).resolve().parent
common_path = CONTROL.parent/'flex-video/engine.py'
if not common_path.exists():
    common_path = CONTROL.parent/'flex_video/engine.py'
spec = importlib.util.spec_from_file_location('quality_common', common_path)
common = importlib.util.module_from_spec(spec); spec.loader.exec_module(common)
Failure = common.Failure


class Engine(common.Engine):
    def __init__(self, root, worker_token, base='http://127.0.0.1:18262'):
        super().__init__(root, worker_token, base)
        # A restart cannot safely resume remote inference. Retain opaque handles,
        # erase abandoned private content and leave state unknown for reconciliation.
        for row in self.db.execute("SELECT id FROM jobs WHERE state='unknown'").fetchall():
            job = self.root/row['id']
            if job.is_dir() and not job.is_symlink():
                for path in job.iterdir():
                    if path.is_file() and path.name not in ('result.json', 'result.mp4') and not re.fullmatch(r'remote-\d+\.json', path.name):
                        path.unlink(missing_ok=True)

    def broker(self, method, path, owner, payload=None, idem=None, dest=None):
        headers = {'Authorization': 'Bearer '+self.token, 'X-Resource-Owner': owner, 'Content-Type': 'application/json'}
        if idem:
            headers['Idempotency-Key'] = idem
        req = urllib.request.Request(self.base+path, method=method, headers=headers,
                                     data=json.dumps(payload).encode() if payload is not None else None)
        try:
            with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req, timeout=30) as response:
                if dest is not None:
                    total = 0
                    with dest.open('xb') as stream:
                        while block := response.read(512*1024):
                            total += len(block)
                            if total > 128*1024**2:
                                raise Failure('quality_segment_output_limit')
                            stream.write(block)
                    return None
                raw = response.read(1024**2+1)
                if len(raw) > 1024**2:
                    raise Failure('quality_worker_payload_limit')
                return json.loads(raw)
        except urllib.error.HTTPError as error:
            raise Failure('not_found' if error.code == 404 else 'quality_worker_rejected') from None
        except (OSError, ValueError):
            raise Failure('submission_uncertain' if method == 'POST' else 'quality_worker_offline') from None

    def capabilities(self):
        cap = self.broker('GET', '/capabilities', hashlib.sha256(b'quality-readiness').hexdigest())
        profiles = cap.get('quality_profiles', {})
        ready = all(k in profiles for k in ('video.generate', 'video.animate_image'))
        return {'ok': ready, **({} if ready else {'error': {'code': 'quality_profile_not_qualified'}}),
                'provider': 'Harmonika Ai', 'quality': 'high', 'preview_fallback': False,
                'video_generation': 'video.generate' in profiles, 'image_to_video': 'video.animate_image' in profiles,
                'quality_profiles': profiles, 'default_resolution': {'width': 1280, 'height': 720},
                'duration_seconds_range': {'minimum': 1, 'mode': 'segmented', 'admission': 'available_disk'},
                'native_video_audio': False, 'audio_requires_narration_step': True,
                'seamless_continuity_guaranteed': False, 'public_enabled': False}

    def remote_quality(self, jid, index, owner, payload, job):
        marker = job/f'remote-{index}.json'
        idem = hashlib.sha256((jid+':'+str(index)).encode()).hexdigest()[:32]
        if marker.exists():
            rid = json.loads(marker.read_text())['job_id']
        else:
            try:
                response = self.broker('POST', '/jobs', owner, payload, idem)
            except Failure as error:
                if str(error) != 'submission_uncertain':
                    raise
                try:
                    response = self.broker('GET', '/requests/'+idem, owner)
                except Failure:
                    raise Failure('submission_uncertain') from None
            rid = response.get('id')
            if not isinstance(rid, str) or not re.fullmatch('[a-f0-9]{32}', rid):
                raise Failure('invalid_quality_worker_response')
            marker.write_text(json.dumps({'job_id': rid})); marker.chmod(0o600)
        if not isinstance(rid, str) or not re.fullmatch('[a-f0-9]{32}', rid):
            raise Failure('invalid_quality_worker_response')
        start = time.monotonic()
        while True:
            try:
                result = self.broker('GET', '/jobs/'+rid, owner)
            except Failure as error:
                if str(error) in ('quality_worker_offline', 'not_found'):
                    raise Failure('worker_timeout_uncertain') from None
                raise
            state = result.get('state')
            if state == 'completed':
                return rid, result['result']
            if state == 'unknown':
                raise Failure('worker_timeout_uncertain')
            if state == 'failed':
                code = result.get('error_type')
                raise Failure(code if code in {'insufficient_vram', 'gpu_busy', 'quality_profile_not_qualified'} else 'quality_generation_failed')
            if time.monotonic()-start > 2700:
                raise Failure('worker_timeout_uncertain')
            time.sleep(5)

    def run(self, jid):
        job = self.root/jid; self.update(jid, state='running')
        data = json.loads((job/'input.json').read_text())
        with self.lock:
            owner = self.db.execute('SELECT owner FROM jobs WHERE id=?', (jid,)).fetchone()[0]
        remaining = data['duration_seconds']; done = 0; index = 0; families = []; uncertain = False
        try:
            image = data.get('image_base64')
            cap = self.capabilities()
            if not cap['ok']:
                raise Failure('quality_profile_not_qualified')
            target_fps = cap['quality_profiles'][data['kind']]['fps']
            manifest = job/'segments.txt'
            with manifest.open('w') as listing:
                while remaining:
                    if shutil.disk_usage(job).free < 2*1024**3:
                        raise Failure('storage_full')
                    kind = 'video.animate_image' if image is not None else 'video.generate'
                    p = cap['quality_profiles'][kind]
                    span = min(remaining, p['max_segment_seconds'])
                    payload = {'kind': kind, 'prompt': data['prompt'], 'duration_seconds': span}
                    if image is not None:
                        payload['image_base64'] = image
                    rid, meta = self.remote_quality(jid, index, owner, payload, job)
                    if (meta.get('quality') != 'high' or meta.get('width') != 1280 or meta.get('height') != 720
                            or meta.get('upscaled') is not False):
                        raise Failure('quality_profile_mismatch')
                    raw = job/'raw.mp4'
                    self.broker('GET', '/jobs/'+rid+'/file', owner, dest=raw)
                    if hashlib.sha256(raw.read_bytes()).hexdigest() != meta.get('sha256'):
                        raise Failure('quality_checksum_mismatch')
                    if common.duration(raw)+.05 < span:
                        raise Failure('worker_video_too_short')
                    segment = job/f'{index:06}.mp4'
                    # Never upscale a preview. Higher-fps source may be downsampled for concatenation.
                    common.command(['ffmpeg', '-v', 'error', '-y', '-i', str(raw), '-t', str(span), '-an',
                                    '-map_metadata', '-1', '-vf', f'fps={target_fps}', '-c:v', 'libx264', '-crf', '17', str(segment)])
                    if abs(common.duration(segment)-span) > .07:
                        raise Failure('segment_duration_mismatch')
                    listing.write("file '%s'\n" % segment.name); listing.flush()
                    common.command(['ffmpeg', '-v', 'error', '-y', '-sseof', '-0.1', '-i', str(segment), '-frames:v', '1', str(job/'next.png')])
                    image = base64.b64encode((job/'next.png').read_bytes()).decode()
                    families.append(meta['model']); raw.unlink(); remaining -= span; done += span; index += 1
                    self.update(jid, progress=done)
            out = job/'result.mp4'
            common.command(['ffmpeg', '-v', 'error', '-y', '-f', 'concat', '-safe', '1', '-i', str(manifest),
                            '-map_metadata', '-1', '-c', 'copy', '-movflags', '+faststart', str(out)])
            actual = common.duration(out)
            if abs(actual-data['duration_seconds']) > .07:
                raise Failure('final_duration_mismatch')
            probe = json.loads(common.command(['ffprobe', '-v', 'error', '-count_frames', '-show_streams', '-of', 'json', str(out)]))
            stream = next(s for s in probe['streams'] if s['codec_type'] == 'video')
            if stream['width'] != 1280 or stream['height'] != 720 or int(stream['nb_read_frames']) < data['duration_seconds']*target_fps:
                raise Failure('quality_artifact_mismatch')
            digest = hashlib.sha256()
            with out.open('rb') as artifact:
                while block := artifact.read(1024**2):
                    digest.update(block)
            result = {'duration_seconds': actual, 'frames': int(stream['nb_read_frames']), 'width': 1280, 'height': 720,
                      'fps': target_fps, 'audio': False, 'quality': 'high', 'upscaled': False, 'models': list(dict.fromkeys(families)),
                      'sha256': digest.hexdigest(), 'bytes': out.stat().st_size,
                      'segmented': index > 1, 'seamless_continuity_guaranteed': False}
            (job/'result.json').write_text(json.dumps(result))
            self.update(jid, state='completed')
        except Exception as error:
            code = str(error) if isinstance(error, Failure) else 'quality_processing_failed'
            uncertain = code in ('submission_uncertain', 'worker_timeout_uncertain')
            self.update(jid, state='unknown' if uncertain else 'failed', error=code)
        finally:
            for path in job.iterdir():
                keep_marker = uncertain and re.fullmatch(r'remote-\d+\.json', path.name)
                if path.name not in ('result.mp4', 'result.json') and not keep_marker and path.is_file():
                    path.unlink(missing_ok=True)
