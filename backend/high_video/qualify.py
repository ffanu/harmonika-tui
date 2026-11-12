"""Activate profiles only from completed, independently decoded local GPU evidence."""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess

CONTROL = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('quality_workflow', CONTROL/'workflow.py')
wf = importlib.util.module_from_spec(spec); spec.loader.exec_module(wf)
JOBS = Path(r'C:\HarmonikaVideo\jobs')
FFMPEG = r'C:\Program Files\WinGet\Links\ffmpeg.exe'
FFPROBE = r'C:\Program Files\WinGet\Links\ffprobe.exe'


def evidence(jid, kind, maximum):
    if len(jid) != 32 or any(c not in '0123456789abcdef' for c in jid):
        raise ValueError('invalid_job_id')
    job = JOBS/jid
    source = json.loads((job/'input.json').read_text())
    result = json.loads((job/'result.json').read_text())
    proof_hash = result.get('workflow_sha256')
    p = wf.profile(result['family']); wf.verify_models(result['family'])
    if (source['kind'] != kind or result.get('status') != 'completed'
            or proof_hash != wf.workflow_hash()
            or not 1 <= maximum <= 10 or result['duration_seconds'] < maximum
            or any(result.get(k) != p[k] for k in ('width', 'height', 'fps', 'steps', 'quality', 'upscaled'))):
        raise ValueError('unqualified_evidence')
    artifact = job/'result.mp4'
    digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
    if digest != result['sha256']:
        raise ValueError('artifact_checksum_mismatch')
    probe = subprocess.run([FFPROBE, '-v', 'error', '-count_frames', '-show_entries',
                            'format=duration:stream=width,height,nb_read_frames', '-of', 'json', str(artifact)],
                           capture_output=True, check=True, timeout=120)
    data = json.loads(probe.stdout); stream = data['streams'][0]
    if (stream['width'] != 1280 or stream['height'] != 720
            or float(data['format']['duration']) < maximum
            or int(stream['nb_read_frames']) < maximum*p['fps']):
        raise ValueError('invalid_evidence_frames')
    subprocess.run([FFMPEG, '-v', 'error', '-i', str(artifact), '-f', 'null', '-'],
                   check=True, timeout=180, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return {**{k: p[k] for k in ('family', 'width', 'height', 'fps', 'steps')},
            'status': 'verified', 'max_segment_seconds': maximum, 'evidence_job': jid,
            'evidence_sha256': digest, 'decoded_frames': int(stream['nb_read_frames'])}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--generate-job', required=True)
    parser.add_argument('--animate-job', required=True)
    parser.add_argument('--generate-seconds', required=True, type=int)
    parser.add_argument('--animate-seconds', required=True, type=int)
    parser.add_argument('--visual-reviewed', action='store_true', required=True,
                        help='operator inspected both decoded clips for visible artifacts')
    args = parser.parse_args()
    result = {'workflow_sha256': wf.workflow_hash(), 'visual_reviewed': True, 'active': {
        'video.generate': evidence(args.generate_job, 'video.generate', args.generate_seconds),
        'video.animate_image': evidence(args.animate_job, 'video.animate_image', args.animate_seconds)}}
    target = CONTROL/'quality-profile.json'
    if target.exists():
        (CONTROL/'quality-profile.previous.json').write_bytes(target.read_bytes())
    temporary = CONTROL/'.quality-profile.tmp'; temporary.write_text(json.dumps(result))
    os.replace(temporary, target)
    print(json.dumps({'ok': True, 'qualified': result['active']}))


if __name__ == '__main__':
    main()
