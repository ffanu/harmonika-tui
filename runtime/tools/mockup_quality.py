"""Bounded adapter for existing responsive QA and optional exact mockup frames."""
import json
from pathlib import Path
import subprocess


def invoke(args, node, here, output_default):
    args=args or {};url=args.get('url')
    if not isinstance(url,str) or not url:return {'ok':False,'error':'url_required'}
    output=args.get('out_dir') or str(output_default)
    if not isinstance(output,str):return {'ok':False,'error':'invalid_output_directory'}
    command=[str(node),str(Path(here)/'qa_matrix.cjs'),'--url',url,'--out',output]
    if 'frames' in args:
        if not isinstance(args['frames'],list):return {'ok':False,'error':'invalid_capture_frames'}
        frames=json.dumps(args['frames'],ensure_ascii=True)
        if len(frames)>12000:return {'ok':False,'error':'invalid_capture_frames'}
        command+=['--frames',frames]
    try:
        result=subprocess.run(command,capture_output=True,timeout=600)
    except subprocess.TimeoutExpired:return {'ok':False,'error':'qa_matrix_timeout','uncertain':True}
    except OSError:return {'ok':False,'error':'qa_matrix_runtime_unavailable'}
    if len(result.stdout)>262144:return {'ok':False,'error':'qa_matrix_oversized_result'}
    try:
        value=json.loads(result.stdout)
        if not isinstance(value,dict):raise ValueError()
        return value
    except (ValueError,UnicodeError):return {'ok':False,'error':'qa_matrix_failed'}
