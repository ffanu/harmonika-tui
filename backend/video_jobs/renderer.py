"""Private H3 adapter. No global ComfyUI interrupts, no host commands or log payloads."""
import hashlib, json, os, time, urllib.request
from pathlib import Path
from fractions import Fraction

class RenderError(Exception):
    pass


def frame_count(seconds):
    target = seconds * 24
    return target + (5 - target) % 17


def request(port, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(f'http://127.0.0.1:{int(port)}{path}', data=data,
                                 headers={'Content-Type': 'application/json'})
    with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req, timeout=12) as r:
        raw = r.read(16 * 1024 * 1024 + 1)
    if len(raw) > 16 * 1024 * 1024:
        raise RenderError('engine_response_limit')
    return json.loads(raw)


def _trim_video(source, destination, seconds):
    """Exactly requested frames; intentionally silent preview, no fake audio claim."""
    import av
    target = seconds * 24
    with av.open(str(source)) as src, av.open(str(destination), 'w') as dst:
        original = src.streams.video[0]
        stream = dst.add_stream('libx264', rate=24)
        stream.width, stream.height = original.width, original.height
        stream.pix_fmt = 'yuv420p'
        stream.options = {'crf': '19', 'preset': 'fast'}
        count = 0
        for frame in src.decode(video=0):
            if count >= target:
                break
            frame.pts = count
            frame.time_base = Fraction(1, 24)
            for packet in stream.encode(frame):
                dst.mux(packet)
            count += 1
        if count != target:
            raise RenderError('video_too_short')
        for packet in stream.encode():
            dst.mux(packet)
    with av.open(str(destination)) as check:
        video = check.streams.video[0]
        count = sum(1 for _ in check.decode(video=0))
        if count != target or abs(float(video.duration * video.time_base) - seconds) > .05:
            raise RenderError('duration_verification_failed')
        dimensions = (video.width, video.height)
    size = destination.stat().st_size
    if not 1000 <= size <= 50 * 1024 * 1024:
        raise RenderError('artifact_size_invalid')
    return {'mime_type': 'video/mp4', 'size_bytes': size,
            'sha256': hashlib.sha256(destination.read_bytes()).hexdigest(),
            'width': dimensions[0], 'height': dimensions[1], 'fps': 24, 'frames': target,
            'duration_seconds': seconds, 'rendered_duration_seconds': frame_count(seconds)/24,
            'preview': True, 'audio': False}


def trim_video(source, destination, seconds):
    partial = destination.with_name(destination.stem+'.partial.mp4')
    try:
        result = _trim_video(source, partial, seconds)
        os.replace(partial, destination)
        return result
    except RenderError:
        raise
    except Exception:
        raise RenderError('encode_failed') from None
    finally:
        partial.unlink(missing_ok=True)


def render(root, output, job_id, payload, update, timeout=1800):
    try:
        config = json.loads((root / 'qualification-status.json').read_text())
        port = config['port']
        flow = json.loads((root / 'smoke-workflow.json').read_text())
        for key in ['140:124','140:131','140:129','92']:assert key in flow
    except (OSError,ValueError,KeyError,AssertionError):
        raise RenderError('workflow_invalid') from None
    try:
        stats = request(port, '/system_stats')
    except Exception:
        raise RenderError('engine_offline') from None
    if stats['system'].get('ram_free', 0) < 8 * 1024**3:
        raise RenderError('system_memory_low')
    if not any(d.get('vram_free', 0) >= 1024**3 for d in stats.get('devices', [])):
        raise RenderError('gpu_memory_full')
    queue = request(port, '/queue')
    if queue.get('queue_running') or queue.get('queue_pending'):
        raise RenderError('gpu_busy')
    flow['140:124']['inputs']['steps'] = 8
    flow['140:131']['inputs'].update(prompt=payload['prompt'], width=320, height=192,
                                    length=frame_count(payload['duration_seconds']))
    flow['140:129']['inputs']['noise_seed'] = int(job_id[:8], 16)
    flow['92']['inputs']['filename_prefix'] = 'harmonika-web-video/' + job_id
    update('dispatching')
    # A failed POST is ambiguous. Never auto-submit it again.
    try:
        ack = request(port, '/prompt', {'prompt': flow, 'client_id': 'hai-video-' + job_id})
    except Exception:
        raise RenderError('dispatch_unknown') from None
    prompt_id = ack.get('prompt_id')
    if not prompt_id or ack.get('node_errors'):
        raise RenderError('workflow_rejected')
    update('rendering', prompt_id)
    started = time.monotonic()
    while time.monotonic() - started < timeout:
        try:
            result = request(port, '/history/' + prompt_id).get(prompt_id)
        except Exception:
            time.sleep(5)
            continue
        if result:
            if result.get('status', {}).get('status_str') == 'error':
                raise RenderError('generation_failed')
            base = (root / 'ComfyUI/output').resolve()
            paths = []
            for row in result.get('outputs', {}).values():
                for key in ('videos', 'gifs', 'images'):
                    for item in row.get(key, []):
                        path = (base / item.get('subfolder', '') / item.get('filename', '')).resolve()
                        if (item.get('type', 'output') == 'output' and path.is_relative_to(base)
                                and path.suffix == '.mp4' and path.stem.startswith(job_id + '_')):
                            paths.append(path)
            paths = list(set(paths))
            if len(paths) != 1:
                raise RenderError('artifact_missing')
            update('encoding')
            return trim_video(paths[0], output / (job_id + '.mp4'), payload['duration_seconds'])
        time.sleep(3)
    raise RenderError('render_unknown')
