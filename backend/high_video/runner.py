"""Submit one owned ComfyUI job, verify real 720p frames, erase owned input/history."""
import base64
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid

CONTROL = Path(__file__).resolve().parent
COMFY = Path(r'C:\HarmonikaVideo\quality-comfy')
JOBS = Path(r'C:\HarmonikaVideo\jobs')
FFMPEG = r'C:\Program Files\WinGet\Links\ffmpeg.exe'
FFPROBE = r'C:\Program Files\WinGet\Links\ffprobe.exe'
BASE = 'http://127.0.0.1:18193'
spec = importlib.util.spec_from_file_location('quality_workflow', CONTROL/'workflow.py')
wf = importlib.util.module_from_spec(spec); spec.loader.exec_module(wf)


def request(method, path, data=None):
    req = urllib.request.Request(BASE+path, method=method,
                                headers={'Content-Type': 'application/json'},
                                data=json.dumps(data).encode() if data is not None else None)
    try:
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req, timeout=15) as response:
            raw = response.read(8*1024**2+1)
            if len(raw) > 8*1024**2:
                raise wf.Failure('comfy_payload_limit')
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError:
        raise wf.Failure('comfy_request_rejected') from None
    except (OSError, ValueError):
        raise wf.Failure('comfy_submission_uncertain' if method == 'POST' and path == '/prompt' else 'comfy_unavailable') from None


def upload(image, jid):
    if not 100 <= len(image) <= 8*1024**2:
        raise wf.Failure('invalid_reference_image')
    boundary = 'hq'+uuid.uuid4().hex
    filename = 'harmonika_hq_'+jid+'.png'
    body = (f'--{boundary}\r\nContent-Disposition: form-data; name="image"; filename="{filename}"\r\nContent-Type: image/png\r\n\r\n'.encode()
            +image+f'\r\n--{boundary}--\r\n'.encode())
    req = urllib.request.Request(BASE+'/upload/image', method='POST', data=body,
                                headers={'Content-Type': 'multipart/form-data; boundary='+boundary})
    try:
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req, timeout=20) as response:
            result = json.loads(response.read(4096))
        if result.get('name') != filename or result.get('subfolder', ''):
            raise ValueError()
        return filename
    except (OSError, ValueError):
        raise wf.Failure('reference_upload_failed') from None


def probe(path):
    result = subprocess.run([FFPROBE, '-v', 'error', '-count_frames', '-show_entries',
                             'format=duration:stream=codec_type,width,height,nb_read_frames', '-of', 'json', str(path)],
                            capture_output=True, check=True, timeout=120)
    meta = json.loads(result.stdout); stream = next(s for s in meta['streams'] if s['codec_type'] == 'video')
    return float(meta['format']['duration']), stream


def run(job):
    data = json.loads((job/'input.json').read_text())
    family = data['family']; seconds = data['duration_seconds']; jid = job.name
    wf.verify_models(family)
    p = wf.profile(family)
    prompt_id = str(uuid.UUID(hex=jid))
    marker = job/'comfy-job.json'
    if marker.exists():
        raise wf.Failure('comfy_reconciliation_required')
    image_name = None; submitted = False; finished = False; owned_outputs = []
    try:
        wait_start = time.monotonic()
        while True:
            q = request('GET', '/queue')
            if not q.get('queue_running') and not q.get('queue_pending'):
                break
            if time.monotonic()-wait_start > 300:
                raise wf.Failure('gpu_busy')
            time.sleep(5)
        if data.get('kind') == 'video.animate_image':
            image_name = upload((job/'input.png').read_bytes(), jid)
        nodes = wf.build(family, data['prompt'], seconds, jid, image_name)
        workflow_sha = wf.workflow_hash()
        marker.write_text(json.dumps({'prompt_id': prompt_id, 'state': 'submitting', 'workflow_sha256': workflow_sha}))
        submitted = True
        result = request('POST', '/prompt', {'prompt_id': prompt_id, 'client_id': 'harmonika-hq-'+jid, 'prompt': nodes})
        if result.get('prompt_id') != prompt_id:
            raise wf.Failure('comfy_submission_uncertain')
        marker.write_text(json.dumps({'prompt_id': prompt_id, 'state': 'accepted', 'workflow_sha256': workflow_sha}))
        start = time.monotonic()
        while True:
            row = request('GET', '/history/'+prompt_id).get(prompt_id)
            if row:
                status = row.get('status', {})
                if status.get('status_str') == 'error':
                    # Only a fixed technical signature leaves RAM; raw errors may include input.
                    messages = status.get('messages', [])
                    oom = any('outofmemory' in str(m).lower() or 'out of memory' in str(m).lower() for m in messages)
                    finished = True
                    raise wf.Failure('insufficient_vram' if oom else 'comfy_inference_failed')
                if status.get('completed'):
                    finished = True
                    for output in row.get('outputs', {}).values():
                        for value in output.values():
                            if isinstance(value, list):
                                for item in value:
                                    if isinstance(item, dict) and str(item.get('filename', '')).endswith('.mp4'):
                                        path = (COMFY/'output'/item.get('subfolder', '')/item['filename']).resolve()
                                        if not path.is_relative_to((COMFY/'output').resolve()) or not path.name.startswith('harmonika_hq_'+jid):
                                            raise wf.Failure('invalid_comfy_artifact')
                                        owned_outputs.append(path)
                    break
            if time.monotonic()-start > 2100:
                raise wf.Failure('comfy_timeout_uncertain')
            time.sleep(5)
        if len(owned_outputs) != 1:
            raise wf.Failure('missing_comfy_artifact')
        subprocess.run([FFMPEG, '-v', 'error', '-i', str(owned_outputs[0]), '-t', str(seconds),
                        '-an', '-map_metadata', '-1', '-vf', f"crop={p['width']}:{p['height']}",
                        '-c:v', 'libx264', '-crf', '17', '-pix_fmt', 'yuv420p', '-movflags', '+faststart',
                        '-y', str(job/'result.mp4')], check=True, timeout=180, stdin=subprocess.DEVNULL,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        actual, stream = probe(job/'result.mp4')
        if (abs(actual-seconds) > .07 or stream['width'] != p['width'] or stream['height'] != p['height']
                or int(stream['nb_read_frames']) < seconds*p['fps']):
            raise wf.Failure('quality_artifact_mismatch')
        subprocess.run([FFMPEG, '-v', 'error', '-i', str(job/'result.mp4'), '-f', 'null', '-'],
                       check=True, timeout=180, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return {'status': 'completed', **p, 'workflow_sha256': workflow_sha, 'duration_seconds': actual, 'frames': int(stream['nb_read_frames']),
                'audio': False, 'bytes': (job/'result.mp4').stat().st_size,
                'sha256': hashlib.sha256((job/'result.mp4').read_bytes()).hexdigest()}
    finally:
        if finished:
            request('POST', '/history', {'delete': [prompt_id]})
            for path in owned_outputs:
                path.unlink(missing_ok=True)
        if not submitted or finished:
            if image_name:
                (COMFY/'input'/image_name).unlink(missing_ok=True)
        # Active/uncertain Comfy work keeps its opaque marker for reconciliation.


if __name__ == '__main__':
    job = Path(sys.argv[1])
    if job.parent != JOBS or len(job.name) != 32:
        raise SystemExit(2)
    try:
        result = run(job)
    except Exception as exc:
        code = str(exc) if isinstance(exc, wf.Failure) else 'quality_runner_failed'
        result = {'status': 'failed', 'error_code': code}
    (job/'result.json').write_text(json.dumps(result))
    raise SystemExit(0 if result['status'] == 'completed' else 1)
