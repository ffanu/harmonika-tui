"""Hot-loaded only after existing signed owner authorization; no preview fallback."""
import importlib.util
import json
from pathlib import Path
import urllib.request

ENABLED = Path('/etc/harmonika-ai/high-video.enabled')
BASE = 'http://127.0.0.1:18266/rpc'


def call(action, owner, **values):
    try:
        req = urllib.request.Request(BASE, method='POST',
                                     headers={'Content-Type': 'application/json', 'Authorization': 'Bearer '+Path('/etc/harmonika-ai/video-toolkit.token').read_text().strip()},
                                     data=json.dumps({'action': action, 'owner': owner, **values}).encode())
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req, timeout=15) as response:
            return json.loads(response.read(1024**2))
    except Exception:
        return {'ok': False, 'provider': 'Harmonika Ai', 'error': {'code': 'high_video_unavailable'}}


def route(data, owner):
    if not ENABLED.is_file():
        return None
    op = data.get('operation'); jid = data.get('job_id', '')
    if op == 'video_capabilities':
        result = call('capabilities', owner)
        if result.get('ok'):
            result.update(status='ok', audience='internal_owner', audio=False, can_add_speech=False,
                          preferred_prompt_language='en', reference_recommended_for_product_fidelity=True,
                          audio_requires_studio=True, flexible_duration=True, duration_seconds=[1, 10],
                          image_to_video_duration_seconds=[1, 10],
                          image_to_video_duration_range=result.get('duration_seconds_range'))
        return result
    if op in ('generate_video', 'animate_video'):
        seconds = data.get('duration_seconds', 1)
        if type(seconds) is not int or not 1 <= seconds <= 2**31-1:
            return {'ok': False, 'error': {'code': 'invalid_duration'}}
        if set(data)-{'operation', 'owner_context', 'prompt', 'duration_seconds', 'request_id', 'reference_image_base64'}:
            return {'ok': False, 'error': {'code': 'invalid_input'}}
        payload = {'kind': 'video.animate_image' if op == 'animate_video' else 'video.generate',
                   'prompt': data.get('prompt'), 'duration_seconds': seconds}
        if op == 'animate_video':
            payload['image_base64'] = data.get('reference_image_base64')
        result = call('submit', owner, request_id=data.get('request_id'), input=payload)
        if result.get('ok'):
            result.update(request_id=data.get('request_id'), retry_submission=False)
        return result
    if op == 'video_request_status':
        result = call('request', owner, request_id=data.get('request_id'))
        return None if result.get('error', {}).get('code') == 'not_found' else result
    if isinstance(jid, str) and jid.startswith('d04a') and op in ('video_job_status', 'download_video', 'narrate_video'):
        result = call('job', owner, job_id=jid)
        if result.get('error', {}).get('code') == 'not_found':
            return None  # Existing flex/legacy GET recovery, never a new submission.
        if not result.get('ok') or op == 'video_job_status':
            return result
        if op == 'download_video':
            return call('download', owner, job_id=jid, offset=data.get('offset', 0))
        return {'ok': False, 'error': {'code': 'flex_video_narration_requires_studio'}}
    return None
